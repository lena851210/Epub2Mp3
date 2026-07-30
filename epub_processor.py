# -*- coding: utf-8 -*-
"""
模块2：EPUB 处理 - 读取、解析、章节提取、文本清理
"""

import os
import re
from typing import Dict, Any, List, Tuple, Optional, Callable

from ebooklib import epub
try:
    from ebooklib import ITEM_DOCUMENT
except Exception:
    ITEM_DOCUMENT = getattr(epub, "ITEM_DOCUMENT", None)

from models import (
    normalize_whitespace,
    clean_text_from_html_bytes,
    sanitize_filename
)

# 全局变量：最后输出目录
last_output_dir = None

# ====== EPUB->TXT 后处理规则（可调参数） ======

# "看起来像章节标题"的段落（用于把超大章节按内部标题切开）
CHAPTER_HEADING_RE = re.compile(
    r"^\s*(第[0-9一二三四五六七八九十百千万零〇两]+[章节回卷篇部].{0,30}|Chapter\s+\d+.*|CHAPTER\s+\d+.*)\s*$"
)

# 很短的章节，低于这个字数就认为"可能是目录/扉页/空页/碎片"，会尝试合并到下一章
MIN_CHAPTER_CHARS = 350

# 很短且疑似"噪音页"的标题关键字（只有很短时才丢弃）
NOISE_TITLE_RE = re.compile(
    r"(目录|封面|版权|扉页|出版|前言|序|推荐|致谢|引言|插图|图表|索引)",
    re.IGNORECASE
)

# “部分/卷/篇”类父级结构标题，尽量不单独导出
VOLUME_ONLY_RE = re.compile(
    r"^\s*第[0-9一二三四五六七八九十百千万零〇两]+[部卷篇编册集]\s*$"
)

# 图题 / 表题 / 图片说明等，避免误判为章节
CAPTION_LIKE_RE = re.compile(
    r"^\s*("
    r"(图|表|插图|附图|图片|照片|Figure|Fig\.?|Table)\s*[\dA-Za-z一二三四五六七八九十零〇两\.\-]*"
    r"(\s*[:：\-—\.]\s*.*)?"
    r"|来源\s*[:：].*"
    r"|注\s*[:：].*"
    r"|说明\s*[:：].*"
    r")\s*$",
    re.IGNORECASE
)


def _count_chars(s: str) -> int:
    return len(re.sub(r"\s+", "", s or ""))


def is_volume_only_title(title: str) -> bool:
    """
    判断是否属于“第一部分 / 第二卷 / 第三篇”这类结构层级标题，
    这类标题通常不应该单独输出成 TXT。
    """
    t = normalize_whitespace(title or "").strip()
    if not t:
        return False

    if VOLUME_ONLY_RE.match(t):
        return True

    # 允许少量后缀，但不要太长
    if re.match(r"^\s*第[0-9一二三四五六七八九十百千万零〇两]+[部卷篇编册集][：:\-\s].{0,20}\s*$", t):
        return True

    return False


def is_caption_like_text(text: str) -> bool:
    """
    判断文本是否更像图题/表题/说明文字，而不是章节标题。
    """
    t = normalize_whitespace(text or "").strip()
    if not t:
        return False
    if len(t) > 120:
        return False
    if CAPTION_LIKE_RE.match(t):
        return True
    return False


def split_chapter_by_internal_headings(ch: Dict[str, Any]) -> List[Dict[str, Any]]:
    """
    如果一个章节正文中出现多个"第X章/第X节"之类标题段落，则按标题切成多个子章节。
    """
    title = (ch.get("title") or "").strip()
    content = (ch.get("content") or "").strip()
    hrefs = ch.get("hrefs", []) or []

    if not content:
        return []

    paras = [p.strip() for p in content.split("\n\n") if p.strip()]
    if len(paras) < 4:
        return [ch]

    parts: List[Dict[str, Any]] = []
    cur_title = title
    buf: List[str] = []

    # 防止误切：每一段至少要积累一定正文后，遇到新标题才真正切开
    MIN_BODY_BEFORE_SPLIT = 800

    def flush():
        nonlocal buf, cur_title
        txt = normalize_whitespace("\n\n".join(buf).strip())
        if txt:
            parts.append({"title": cur_title or title, "content": txt, "hrefs": hrefs[:]})
        buf = []

    for p in paras:
        # p 很像一个"章节标题"
        if CHAPTER_HEADING_RE.match(p) and _count_chars(p) <= 60:
            # 图题/表题不应作为分章点
            if is_caption_like_text(p):
                buf.append(p)
                continue

            # 如果前面积累的正文足够多，才切分
            if _count_chars("\n\n".join(buf)) >= MIN_BODY_BEFORE_SPLIT:
                flush()
                cur_title = p
                continue
            else:
                # 正文还不够就别切，避免标题孤零零一行变成小文件
                buf.append(p)
                continue

        buf.append(p)

    flush()

    # 如果没真正切出多个部分，就返回原章节
    return parts if len(parts) >= 2 else [ch]


def postprocess_chapters(chapters: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """
    1) 先把"超大章节"按内部标题拆开
    2) 再把"过短章节"合并到下一章（或丢弃噪音页）
    3) 把“第一部分/第二部分”这类父级结构标题并入下一章
    """
    # 1) 拆分
    expanded: List[Dict[str, Any]] = []
    for ch in chapters:
        expanded.extend(split_chapter_by_internal_headings(ch))

    # 2) 合并/过滤短章节 + 卷标题处理
    result: List[Dict[str, Any]] = []
    i = 0
    pending_volume_prefix = ""

    while i < len(expanded):
        ch = expanded[i]
        title = (ch.get("title") or "").strip()
        content = (ch.get("content") or "").strip()
        chars = _count_chars(content)

        # “第一部分/第二卷”这类父级结构标题：不单独保留，作为后续章节前缀
        if title and is_volume_only_title(title):
            if chars < 1200:
                pending_volume_prefix = title
                i += 1
                continue

        # 丢弃：很短 + 标题疑似噪音页（目录/版权等）
        if chars < 800 and title and NOISE_TITLE_RE.search(title):
            i += 1
            continue

        # 如果前面挂了一个“卷/部/篇”标题，就拼到当前真实章节标题上
        if pending_volume_prefix:
            if title:
                if pending_volume_prefix not in title:
                    title = f"{pending_volume_prefix} {title}".strip()
                    ch["title"] = title
            else:
                ch["title"] = pending_volume_prefix
            pending_volume_prefix = ""

        if chars < MIN_CHAPTER_CHARS and i < len(expanded) - 1:
            # 合并到下一章（保持顺序：把短内容放到下一章开头）
            nxt = expanded[i + 1]
            prefix = content
            if title and title not in prefix:
                prefix = title + "\n\n" + prefix if prefix else title

            nxt["content"] = normalize_whitespace((prefix + "\n\n" + (nxt.get("content") or "")).strip())

            # 如果下一章标题很弱，而当前标题更像正式章节标题，可考虑把标题也传过去
            nxt_title = (nxt.get("title") or "").strip()
            if title and (not nxt_title or is_volume_only_title(nxt_title)):
                nxt["title"] = title

            i += 1
            continue

        result.append(ch)
        i += 1

    # 如果最后还有挂起的 volume prefix，通常说明它只是尾部孤立结构页，忽略即可
    return result


# ====== 【函数1】TOC 映射构建 ======
def build_toc_map(book: epub.EpubBook) -> Dict[str, str]:
    """
    构建 EPUB 书籍的 TOC 映射，优先叶子节点。
    返回: {href_key: title} 字典
    """
    href2title: Dict[str, str] = {}

    def walk(items):
        if not items:
            return

        if isinstance(items, (list, tuple)):
            for it in items:
                walk(it)
            return

        try:
            href = getattr(items, "href", None)
            title = normalize_whitespace(getattr(items, "title", None) or "").strip()

            subitems = (
                getattr(items, "subitems", None)
                or getattr(items, "children", None)
                or getattr(items, "items", None)
                or []
            )

            # 先递归处理子项，让叶子优先占位
            if subitems:
                walk(subitems)

            if href:
                href_key = str(href).split("#")[0]

                # 优先采用：
                # 1) 叶子节点
                # 2) 当前 href 还没被记录
                # 避免父级“第一部分”覆盖真正章节
                if href_key not in href2title or not subitems:
                    href2title[href_key] = title

        except Exception:
            return

    try:
        walk(book.toc or [])
    except Exception:
        pass

    return href2title


def build_leaf_toc_map(book: epub.EpubBook) -> Dict[str, str]:
    """
    单独提取 TOC 叶子节点映射。
    返回: {href_key: leaf_title}
    """
    href2title: Dict[str, str] = {}

    def walk(items):
        if not items:
            return

        if isinstance(items, (list, tuple)):
            for it in items:
                walk(it)
            return

        try:
            href = getattr(items, "href", None)
            title = normalize_whitespace(getattr(items, "title", None) or "").strip()
            subitems = (
                getattr(items, "subitems", None)
                or getattr(items, "children", None)
                or getattr(items, "items", None)
                or []
            )

            if subitems:
                walk(subitems)
            else:
                if href:
                    href_key = str(href).split("#")[0]
                    href2title[href_key] = title
        except Exception:
            return

    try:
        walk(book.toc or [])
    except Exception:
        pass

    return href2title


# ====== 【函数2】获取元素字节 ======
def get_item_bytes(item) -> bytes:
    """获取 EPUB 元素的字节内容"""
    if item is None:
        return b""

    for meth in ("get_content", "get_body_content"):
        fn = getattr(item, meth, None)
        if callable(fn):
            try:
                val = fn()
                if isinstance(val, (bytes, bytearray)):
                    return bytes(val)
                if isinstance(val, str):
                    return val.encode("utf-8")
            except Exception:
                continue

    val = getattr(item, "content", None)
    if isinstance(val, (bytes, bytearray)):
        return bytes(val)
    if isinstance(val, str):
        return val.encode("utf-8")

    return b""


# ====== 【函数3】获取元素 href ======
def get_item_href_key(item) -> str:
    """获取 EPUB 元素的 href 键"""
    try:
        name = ""
        fn = getattr(item, "get_name", None)
        if callable(fn):
            name = fn() or ""
        if not name:
            name = getattr(item, "file_name", "") or getattr(item, "href", "") or getattr(item, "id", "") or ""
        return str(name).split("#")[0]
    except Exception:
        return ""


# ====== 【函数4】移除开头标题 ======
def remove_leading_title_from_text(title: str, text: str) -> str:
    """
    从文本中移除开头重复的标题
    """
    if not title or not text:
        return text

    norm_title = normalize_whitespace(title).strip(" \n\r\t")
    norm_text = normalize_whitespace(text)

    if not norm_title or not norm_text:
        return text

    if norm_text.startswith(norm_title):
        title_end_pos = len(norm_title)
        while title_end_pos < len(norm_text) and norm_text[title_end_pos] in " :：.-_———·\n\r\t":
            title_end_pos += 1

        result = norm_text[title_end_pos:].lstrip()
        if len(result) < len(norm_text) * 0.3:
            return text
        return result

    lines = norm_text.split('\n')
    if len(lines) > 1 and normalize_whitespace(lines[0]) == norm_title:
        result = '\n'.join(lines[1:]).lstrip()
        if len(result) < len(norm_text) * 0.3:
            return text
        return result

    if len(lines) > 1:
        first_line_clean = re.sub(r'^\d+\s*', '', normalize_whitespace(lines[0]))
        if first_line_clean == norm_title:
            result = '\n'.join(lines[1:]).lstrip()
            if len(result) < len(norm_text) * 0.3:
                return text
            return result

    pattern = r'^\s*' + re.escape(norm_title) + r'[\s\:\-–—…\._]*\n+'
    new_text = re.sub(pattern, "", norm_text, flags=re.IGNORECASE)

    if re.search(rf'^\d+\s*\n+{re.escape(norm_title)}', norm_text):
        new_text = re.sub(rf'^\d+\s*\n+{re.escape(norm_title)}\s*\n+', '', norm_text)

    if len(new_text) < len(norm_text) * 0.7:
        return text

    return new_text


def dedupe_adjacent_paragraphs(text: str, title: str = "", scan_first_n: int = 12) -> str:
    """
    清理紧挨着的重复段落/重复标题行。
    """
    if not text:
        return text

    paras = [p.strip() for p in re.split(r"\n{2,}", text) if p.strip()]
    if not paras:
        return text

    norm_title = normalize_whitespace(title).strip()

    def _simplify(s: str) -> str:
        s = normalize_whitespace(s).strip()
        s = re.sub(r"[ \t:：\-_—·\(\)\[\]【】<>《》\"']+", "", s)
        return s

    simp_title = _simplify(norm_title) if norm_title else ""

    cleaned = []
    prev_s = None

    for i, p in enumerate(paras):
        sp = _simplify(p)

        if prev_s is not None and sp == prev_s:
            continue

        if simp_title and i < scan_first_n and sp == simp_title:
            continue

        cleaned.append(p)
        prev_s = sp

    return "\n\n".join(cleaned)


def remove_redundant_heading_lines(text: str, title: str = "", scan_first_lines: int = 30) -> str:
    """
    按"行"清理开头重复标题。
    """
    if not text:
        return text

    def simplify(s: str) -> str:
        s = normalize_whitespace(s).strip()
        s = re.sub(r"[ \t:：\-_—·\(\)\[\]【】<>《》\"']+", "", s)
        s = re.sub(r"第[0-9一二三四五六七八九十百千万零〇两]+([章节回卷篇部])", r"第X\1", s)
        return s

    def looks_like_heading(line: str) -> bool:
        ln = (line or "").strip()
        if not ln:
            return False
        if len(ln) > 80:
            return False
        if is_caption_like_text(ln):
            return False
        if CHAPTER_HEADING_RE.match(ln):
            return True
        if len(ln) <= 35 and not any(c in ln for c in "。，；！？.!?") and any(k in ln for k in ("章", "节", "篇", "回", "卷", "部")):
            return True
        return False

    title_s = simplify(title) if title else ""
    lines = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    result = []

    for i, line in enumerate(lines):
        ln = line.strip()

        if i < scan_first_lines and ln and looks_like_heading(ln):
            cur_s = simplify(ln)

            # 当前行如果其实更像图题，不删
            if is_caption_like_text(ln):
                result.append(line)
                continue

            # 情况1：当前行和标题高度相似 -> 删除
            if title_s and (cur_s == title_s or cur_s in title_s or title_s in cur_s):
                continue

            # 情况2：和上一行都是标题，保留更完整的一行
            if result:
                prev = result[-1].strip()
                if prev and looks_like_heading(prev):
                    prev_s = simplify(prev)

                    if cur_s == prev_s:
                        continue

                    if cur_s in prev_s and len(cur_s) < len(prev_s):
                        continue

                    if prev_s in cur_s and len(prev_s) < len(cur_s):
                        result.pop()
                        result.append(line)
                        continue

        result.append(line)

    return normalize_whitespace("\n".join(result))


# ====== 【函数5���从书籍构建章节 ======
def build_chapters_from_book(book: epub.EpubBook) -> List[Dict[str, Any]]:
    """
    从 EPUB 书籍中构建章节列表
    返回: [{"title": str, "content": str, "hrefs": [str]}, ...]
    """
    href_title_map = build_toc_map(book)
    leaf_toc_map = build_leaf_toc_map(book)
    spine = getattr(book, "spine", []) or []
    chapters: List[Dict[str, Any]] = []
    current = None
    pending_volume_title = ""

    def should_merge_with_previous(prev_chapter, curr_chapter):
        """判断是否应合并到前一章"""
        prev_content = prev_chapter.get("content", "")
        curr_content = curr_chapter.get("content", "")
        curr_title = (curr_chapter.get("title") or "").strip()

        if is_volume_only_title(curr_title):
            return True

        if len(curr_content.strip()) < 50 and looks_like_title(curr_content):
            return True
        if len(prev_content.strip()) < 50 and looks_like_body(curr_content):
            return True
        return False

    def looks_like_title(text):
        """判断文本是否像标题"""
        text = normalize_whitespace(text or "").strip()
        if not text:
            return False
        if is_caption_like_text(text):
            return False
        if len(text) < 30 and (text.endswith(('章', '节', '篇', '回', '卷', '部')) or not any(c in text for c in '。，；！？')):
            return True
        if CHAPTER_HEADING_RE.match(text) and len(text) <= 60:
            return True
        return False

    def looks_like_body(text):
        """判断文本是否像正文"""
        text = normalize_whitespace(text or "").strip()
        if len(text) > 100 and any(c in text for c in '。，；！？'):
            return True
        return False

    def finalize_current():
        """完成当前章节处理"""
        nonlocal current, pending_volume_title
        if current:
            joined = "\n\n".join([t for t in current.get("texts", []) if t])
            content = normalize_whitespace(joined)

            title = (current.get("title") or "").strip()

            # 先拼卷标题，但如果当前本身也是卷标题，则不重复拼
            if pending_volume_title and title and not is_volume_only_title(title):
                if pending_volume_title not in title:
                    current["title"] = f"{pending_volume_title} {title}".strip()
                pending_volume_title = ""

            title = (current.get("title") or "").strip()

            # 在章节正式入库前，统一清理"标题重复"
            if content:
                content = remove_leading_title_from_text(title, content)
                content = remove_redundant_heading_lines(content, title)
                content = dedupe_adjacent_paragraphs(content, title)
                content = normalize_whitespace(content)

            current["content"] = content

            # 纯“第一部分”这类结构页，不单独入库，挂到后面章节
            if title and is_volume_only_title(title) and _count_chars(content) < 1200:
                pending_volume_title = title
                current = None
                return

            if len(chapters) > 0 and should_merge_with_previous(chapters[-1], current):
                previous = chapters[-1]
                previous["content"] = normalize_whitespace(
                    previous.get("content", "") + "\n\n" + current.get("content", "")
                )
                previous["hrefs"].extend(current.get("hrefs", []))
                if current.get("title") and len(current["title"]) > len(previous.get("title", "")):
                    previous["title"] = current["title"]
            else:
                chapters.append(current)

            current = None

    # 收集所有文档元素
    all_items = []
    for item_info in spine:
        item_id = item_info[0]
        try:
            item = book.get_item_with_id(item_id)
            if item is None:
                continue
            if ITEM_DOCUMENT is not None:
                try:
                    if item.get_type() != ITEM_DOCUMENT:
                        continue
                except Exception:
                    pass
            all_items.append(item)
        except Exception:
            continue

    # 处理每个元素
    for item in all_items:
        href = get_item_href_key(item)
        content_bytes = get_item_bytes(item)
        if not content_bytes:
            continue

        soup_title, text = clean_text_from_html_bytes(content_bytes)
        text = normalize_whitespace(text or "")
        text_len = len(text.strip())

        toc_title = href_title_map.get(href, "").strip() if href else ""
        leaf_title = leaf_toc_map.get(href, "").strip() if href else ""

        # 优先使用叶子 TOC 标题
        effective_toc_title = leaf_title or toc_title

        # 1) 短小的“第一部分/第二卷”类 TOC 页面：作为前缀，不单独开章
        if effective_toc_title and is_volume_only_title(effective_toc_title) and text_len < 1200:
            if current:
                finalize_current()
            pending_volume_title = effective_toc_title
            continue

        # 2) 正式 TOC 章节：开新章
        if effective_toc_title:
            if current:
                finalize_current()
            current = {"title": effective_toc_title, "texts": [], "hrefs": [href]}
            if text:
                current["texts"].append(text)
            continue

        # 3) 没有 TOC 时，短 soup_title 也可作为候选，但要排除图题/说明文字
        if soup_title:
            soup_title = normalize_whitespace(soup_title).strip()

        if soup_title and text_len < 300 and not is_caption_like_text(soup_title):
            if current and not current.get("texts"):
                current["title"] = soup_title
            else:
                if current:
                    finalize_current()
                current = {"title": soup_title, "texts": [], "hrefs": [href]}
            if text:
                current["texts"].append(text)
            continue

        # 4) 普通正文归入当前章节
        if not current:
            title_guess = ""
            first_line = text.splitlines()[0].strip() if text else ""

            if soup_title and not is_caption_like_text(soup_title):
                title_guess = soup_title
            elif first_line and len(first_line) <= 50 and not is_caption_like_text(first_line):
                title_guess = first_line
            else:
                title_guess = f"第{len(chapters)+1}章"

            current = {"title": title_guess, "texts": [text] if text else [], "hrefs": [href]}
        else:
            if text:
                current["texts"].append(text)
            current["hrefs"].append(href)

    if current:
        finalize_current()

    return chapters


# ====== 【函数6】EPUB 转 TXT ======
def convert_epub_to_txt(
    epub_path: str,
    progress_callback: Optional[Callable] = None,
    max_chars_per_file: int = 50000
) -> Tuple[str, int, int]:
    """
    将 EPUB 文件转换为 TXT 文件

    返回:
        (输出目录, 生成的TXT文件数量, 章节计数)
    """
    global last_output_dir

    try:
        book = epub.read_epub(epub_path)
    except Exception as e:
        raise RuntimeError(f"无法读取EPUB文件: {e}")

    out_dir = os.path.splitext(epub_path)[0] + "_txt"
    os.makedirs(out_dir, exist_ok=True)

    if progress_callback:
        progress_callback("正在解析EPUB结构...")

    chapters = build_chapters_from_book(book)
    if not chapters:
        raise RuntimeError("未能从 EPUB 中提取到任何章节。")

    print("\n===== 原始章节（build_chapters_from_book 后）=====")
    for i, ch in enumerate(chapters, 1):
        print(f"{i:03d} | {ch.get('title', '')[:80]}")

    # 后处理：拆大章、合并/过滤小碎片
    chapters = postprocess_chapters(chapters)

    print("\n===== 后处理章节（postprocess_chapters 后）=====")
    for i, ch in enumerate(chapters, 1):
        print(f"{i:03d} | {ch.get('title', '')[:80]}")

    total_chapters = len(chapters)
    converted_count = 0  # 生成的 TXT 文件数

    def split_by_limit(content: str, limit: int) -> List[str]:
        """把超长 content 切成多个 part（尽量在段落/句末切）"""
        parts: List[str] = []
        start_pos = 0
        L = len(content)

        while start_pos < L:
            end_pos = start_pos + limit
            if end_pos >= L:
                end_pos = L
            else:
                # 优先在段落处断开
                para_end = content.rfind("\n\n", start_pos, end_pos)
                if para_end != -1 and para_end > start_pos + int(limit * 0.7):
                    end_pos = para_end + 2
                else:
                    # 否则在句末断开
                    sentence_end = max(
                        content.rfind("。", start_pos, end_pos),
                        content.rfind("！", start_pos, end_pos),
                        content.rfind("？", start_pos, end_pos),
                        content.rfind(".", start_pos, end_pos),
                        content.rfind("!", start_pos, end_pos),
                        content.rfind("?", start_pos, end_pos),
                    )
                    if sentence_end != -1 and sentence_end > start_pos + int(limit * 0.7):
                        end_pos = sentence_end + 1

            part = content[start_pos:end_pos].strip()
            if part:
                parts.append(part)
            start_pos = end_pos

        return parts if parts else [content.strip()]

    file_counter = 1  # 章节编号（001/002/003...）

    for idx, ch in enumerate(chapters):
        content = normalize_whitespace((ch.get("content", "") or "").strip())
        raw_title = (ch.get("title") or "").strip() or f"第{file_counter}章"

        if not content:
            print(f"跳过空章节: file_counter={file_counter:03d}, 标题={raw_title}")
            continue

        print(f"写入章节: file_counter={file_counter:03d}, 标题={raw_title}")
        safe_title_for_file = sanitize_filename(raw_title, max_len=60)

        # --- 拆分 ---
        if len(content) > max_chars_per_file:
            parts_text = split_by_limit(content, max_chars_per_file)

            min_tail = int(max_chars_per_file * 0.25)
            if len(parts_text) >= 2 and len(parts_text[-1]) < min_tail:
                parts_text[-2] = (parts_text[-2].rstrip() + "\n\n" + parts_text[-1].lstrip()).strip()
                parts_text.pop()

            split_total = len(parts_text)

            for part_num, part_content in enumerate(parts_text, 1):
                filename = (
                    f"{file_counter:03d} {safe_title_for_file}.txt"
                    if split_total <= 1
                    else f"{file_counter:03d}-{part_num} {safe_title_for_file}.txt"
                )
                out_path = os.path.join(out_dir, filename)

                if part_num == 1:
                    part_content = remove_leading_title_from_text(raw_title, part_content)

                part_content = remove_redundant_heading_lines(part_content, raw_title)
                part_content = dedupe_adjacent_paragraphs(part_content, raw_title)
                part_content = normalize_whitespace(part_content)

                try:
                    with open(out_path, "w", encoding="utf-8") as f:
                        header = raw_title if split_total <= 1 else f"{raw_title}（第{part_num}部分）"
                        f.write(header + "\n\n")
                        f.write(part_content if part_content else "(本章无可提取正文)")
                    converted_count += 1
                except Exception as e:
                    print(f"写入失败：{out_path} -> {e}")

            file_counter += 1

        # --- 不拆分 ---
        else:
            filename = f"{file_counter:03d} {safe_title_for_file}.txt"
            out_path = os.path.join(out_dir, filename)

            content2 = remove_leading_title_from_text(raw_title, content)
            content2 = remove_redundant_heading_lines(content2, raw_title)
            content2 = dedupe_adjacent_paragraphs(content2, raw_title)
            content2 = normalize_whitespace(content2)

            try:
                with open(out_path, "w", encoding="utf-8") as f:
                    f.write(raw_title + "\n\n")
                    f.write(content2 if content2 else "(本章无可提取正文)")
                converted_count += 1
                file_counter += 1
            except Exception as e:
                print(f"写入失败：{out_path} -> {e}")

        if progress_callback and idx % 5 == 0:
            progress_callback(f"正在转换: {idx}/{total_chapters}章")

    last_output_dir = out_dir
    return out_dir, converted_count, file_counter - 1