# -*- coding: utf-8 -*-
"""
模块2：EPUB 处理 - 读取、解析、章节提取、文本清理
"""

import os
import re
from typing import Dict, Any, List, Tuple, Optional, Callable
from urllib.parse import unquote

from ebooklib import epub
try:
    from ebooklib import ITEM_COVER, ITEM_DOCUMENT, ITEM_IMAGE
except Exception:
    ITEM_COVER = getattr(epub, "ITEM_COVER", None)
    ITEM_DOCUMENT = getattr(epub, "ITEM_DOCUMENT", None)
    ITEM_IMAGE = getattr(epub, "ITEM_IMAGE", None)

from models import (
    normalize_whitespace,
    clean_text_from_html_bytes,
    sanitize_filename
)

# 全局变量：最后输出目录
last_output_dir = None

SAVED_COVER_BASENAME = ".epub-to-mp3-cover"
SAVED_COVER_EXTENSIONS = (".jpg", ".png", ".webp")

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

# 明确不适合作为音频章节的 EPUB 目录项。
# 前言、序言、致谢等仍可能有收听价值，不在这里一刀切删除。
NON_AUDIO_TOC_TITLE_RE = re.compile(
    r"^\s*(封面|封底|扉页|书名页|版权(?:信息)?|目录)\s*$",
    re.IGNORECASE,
)

# “部分/卷/篇”类父级结构标题，尽量不单独导出。
# 书籍常见“第一部分 科技创新的体系”，“部分”需作为一个完整层级词识别。
VOLUME_ONLY_RE = re.compile(
    r"^\s*第[0-9一二三四五六七八九十百千万零〇两]+(?:部分|部|卷|篇|编|册|集)\s*$"
)
VOLUME_WITH_SUBTITLE_RE = re.compile(
    r"^\s*第[0-9一二三四五六七八九十百千万零〇两]+(?:部分|部|卷|篇|编|册|集)"
    r"(?:\s+|[：:\-—]\s*)[^。！？!?]{1,30}\s*$"
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

    # 允许结构标题后带一个简短副标题，但不匹配完整句子。
    if VOLUME_WITH_SUBTITLE_RE.match(t):
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


def structural_headings_from_page(title: str, content: str) -> List[str]:
    """保留结构页中除主标题以外的简短副标题。"""
    headings: List[str] = []

    def append_unique(value: str):
        normalized = normalize_whitespace(value or "").strip()
        key = _heading_compare_key(normalized)
        if not normalized or not key:
            return
        if any(_heading_compare_key(existing) == key for existing in headings):
            return
        headings.append(normalized)

    append_unique(title)
    title_key = _heading_compare_key(title)

    for paragraph in [p.strip() for p in (content or "").split("\n\n") if p.strip()]:
        paragraph_key = _heading_compare_key(paragraph)
        if not paragraph_key or paragraph_key == title_key:
            continue
        # 目录标题可能是“第一部分 + 主题”，HTML 内则只有“第一部分”。
        if paragraph_key in title_key:
            continue
        if _count_chars(paragraph) <= 80:
            append_unique(paragraph)

    return headings


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
            parts.append({
                "title": cur_title or title,
                "content": txt,
                "hrefs": hrefs[:],
                "toc_confirmed": bool(ch.get("toc_confirmed")),
                # 父级结构标题只在拆分后的第一段开头朗读一次。
                "structural_headings": (
                    list(ch.get("structural_headings", []) or []) if not parts else []
                ),
            })
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
    pending_structural_headings: List[str] = []

    while i < len(expanded):
        ch = expanded[i]
        title = (ch.get("title") or "").strip()
        content = (ch.get("content") or "").strip()
        chars = _count_chars(content)

        # “第一部分/第二卷”这类父级结构标题：不单独保留，放到下一章的朗读开头。
        if title and is_volume_only_title(title):
            if chars < 1200:
                page_headings = structural_headings_from_page(title, content)
                for heading in list(ch.get("structural_headings", []) or []) + page_headings:
                    if heading and heading not in pending_structural_headings:
                        pending_structural_headings.append(heading)
                i += 1
                continue

        # 丢弃：很短 + 标题疑似噪音页（目录/版权等）
        if chars < 800 and title and NOISE_TITLE_RE.search(title):
            # 结构标题不应因紧跟一个被过滤页面而丢失。
            for heading in list(ch.get("structural_headings", []) or []):
                if heading and heading not in pending_structural_headings:
                    pending_structural_headings.append(heading)
            i += 1
            continue

        # 保留真实章节的文件名，另外记录应在它前面朗读的父级标题。
        if pending_structural_headings:
            existing_headings = list(ch.get("structural_headings", []) or [])
            ch["structural_headings"] = pending_structural_headings + [
                heading for heading in existing_headings
                if heading not in pending_structural_headings
            ]
            pending_structural_headings = []

        if (
            not ch.get("toc_confirmed")
            and chars < MIN_CHAPTER_CHARS
            and i < len(expanded) - 1
        ):
            # 合并到下一章（保持顺序：把短内容放到下一章开头）
            nxt = expanded[i + 1]
            prefix = content
            if title and title not in prefix:
                prefix = title + "\n\n" + prefix if prefix else title

            nxt["content"] = normalize_whitespace((prefix + "\n\n" + (nxt.get("content") or "")).strip())

            current_headings = list(ch.get("structural_headings", []) or [])
            if current_headings:
                next_headings = list(nxt.get("structural_headings", []) or [])
                nxt["structural_headings"] = current_headings + [
                    heading for heading in next_headings
                    if heading not in current_headings
                ]

            # 如果下一章标题很弱，而当前标题更像正式章节标题，可考虑把标题也传过去
            nxt_title = (nxt.get("title") or "").strip()
            if title and (not nxt_title or is_volume_only_title(nxt_title)):
                nxt["title"] = title

            i += 1
            continue

        result.append(ch)
        i += 1

    # 如果最后还有挂起的结构标题，通常说明它只是尾部孤立结构页，忽略即可。
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


def build_leaf_toc_entries(book: epub.EpubBook) -> List[Dict[str, str]]:
    """
    按原书顺序提取 TOC 叶子章节，并保留 href 中的锚点。

    很多 EPUB 会把多篇文章放在同一个 HTML 文件中，依靠
    ``chapter.html#section-2`` 区分章节。锚点不能像旧逻辑那样丢弃。
    """
    entries: List[Dict[str, str]] = []

    def walk(items):
        if not items:
            return

        # ebooklib 用 (Section, [children]) 表示有子目录的父级。
        # 父级只负责分组，真正输出音频的是它下面的叶子章节。
        if (
            isinstance(items, tuple)
            and len(items) == 2
            and isinstance(items[1], (list, tuple))
        ):
            walk(items[1])
            return

        if isinstance(items, (list, tuple)):
            for item in items:
                walk(item)
            return

        try:
            subitems = (
                getattr(items, "subitems", None)
                or getattr(items, "children", None)
                or getattr(items, "items", None)
                or []
            )
            if subitems:
                walk(subitems)
                return

            href = str(getattr(items, "href", None) or "").strip()
            title = normalize_whitespace(getattr(items, "title", None) or "").strip()
            if href and title:
                entries.append({"href": href, "title": title})
        except Exception:
            return

    try:
        walk(book.toc or [])
    except Exception:
        pass

    return entries


def _split_toc_href(href: str) -> Tuple[str, str]:
    decoded = unquote(str(href or "").strip())
    file_href, separator, fragment = decoded.partition("#")
    while file_href.startswith("./"):
        file_href = file_href[2:]
    return file_href, fragment if separator else ""


def _is_non_audio_toc_title(title: str) -> bool:
    return bool(NON_AUDIO_TOC_TITLE_RE.match(normalize_whitespace(title or "")))


def _find_fragment_tag_start(html: str, fragment: str) -> Optional[int]:
    if not fragment:
        body_match = re.search(r"<body\b[^>]*>", html, flags=re.IGNORECASE)
        return body_match.end() if body_match else 0

    escaped = re.escape(fragment)
    pattern = re.compile(
        rf"<[^>]*\b(?:id|name)\s*=\s*(?:[\"']{escaped}[\"']|{escaped}(?=[\s>]))[^>]*>",
        flags=re.IGNORECASE,
    )
    match = pattern.search(html)
    return match.start() if match else None


def extract_toc_anchor_segments(
    html_bytes: bytes,
    entries: List[Dict[str, str]],
) -> List[Dict[str, str]]:
    """按同一 HTML 文件中的 TOC 锚点切出独立章节正文。"""
    if not html_bytes or not entries:
        return []

    html = html_bytes.decode("utf-8", errors="ignore")
    located: List[Tuple[int, Dict[str, str]]] = []

    for entry in entries:
        _, fragment = _split_toc_href(entry.get("href", ""))
        start = _find_fragment_tag_start(html, fragment)
        if start is None:
            continue
        located.append((start, entry))

    # 多目录项时必须全部定位成功，否则宁可回退旧流程，也不要静默丢章。
    if len(entries) > 1 and len(located) != len(entries):
        return []

    # 同一个无锚点地址无法可靠切成多章。
    positions = [position for position, _ in located]
    if len(set(positions)) != len(positions):
        return []

    located.sort(key=lambda item: item[0])
    body_end_match = re.search(r"</body\s*>", html, flags=re.IGNORECASE)
    document_end = body_end_match.start() if body_end_match else len(html)
    segments: List[Dict[str, str]] = []

    for index, (start, entry) in enumerate(located):
        end = located[index + 1][0] if index + 1 < len(located) else document_end
        segment_html = f"<html><body>{html[start:end]}</body></html>"
        _, text = clean_text_from_html_bytes(segment_html.encode("utf-8"))
        segments.append(
            {
                "title": entry.get("title", ""),
                "content": text,
                "href": entry.get("href", ""),
            }
        )

    return segments


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


def _cover_extension(item, content: bytes) -> str:
    """根据图片内容和 EPUB 元数据确定封面文件扩展名。"""
    if content.startswith(b"\xff\xd8\xff"):
        return ".jpg"
    if content.startswith(b"\x89PNG\r\n\x1a\n"):
        return ".png"
    if content.startswith(b"RIFF") and content[8:12] == b"WEBP":
        return ".webp"

    media_type = str(getattr(item, "media_type", "") or "").lower()
    if "jpeg" in media_type or "jpg" in media_type:
        return ".jpg"
    if "png" in media_type:
        return ".png"
    if "webp" in media_type:
        return ".webp"

    file_name = str(getattr(item, "file_name", "") or "").lower()
    extension = os.path.splitext(file_name)[1]
    if extension == ".jpeg":
        return ".jpg"
    if extension in SAVED_COVER_EXTENSIONS:
        return extension
    return ""


def _find_epub_cover_item(book: epub.EpubBook):
    """优先查找 EPUB 明确标记的封面图片。"""
    if ITEM_COVER is not None:
        try:
            cover_items = list(book.get_items_of_type(ITEM_COVER))
            if cover_items:
                return cover_items[0]
        except Exception:
            pass

    # 兼容少量没有正确声明 ITEM_COVER、但属性或 ID 明确写着 cover 的 EPUB。
    try:
        for item in book.get_items():
            try:
                item_type = item.get_type()
            except Exception:
                item_type = None
            if ITEM_IMAGE is not None and item_type != ITEM_IMAGE:
                continue

            properties = getattr(item, "properties", None) or []
            if isinstance(properties, str):
                properties = [properties]
            property_text = " ".join(str(value) for value in properties).lower()
            identity = " ".join(
                str(value or "")
                for value in (
                    getattr(item, "id", ""),
                    getattr(item, "file_name", ""),
                )
            ).lower()
            if "cover-image" in property_text or "cover" in identity:
                return item
    except Exception:
        pass
    return None


def save_epub_cover(book: epub.EpubBook, output_dir: str) -> Optional[str]:
    """
    把 EPUB 封面保存为 TXT 目录中的隐藏生成文件，供后续 MP3 使用。

    无封面或格式不支持时返回 None，不影响正文转换。
    """
    os.makedirs(output_dir, exist_ok=True)

    # 这些文件由本程序生成。先清理可避免重新导入无封面 EPUB 时误用旧图。
    for extension in SAVED_COVER_EXTENSIONS:
        stale_path = os.path.join(output_dir, SAVED_COVER_BASENAME + extension)
        if os.path.isfile(stale_path):
            try:
                os.remove(stale_path)
            except Exception:
                pass

    cover_item = _find_epub_cover_item(book)
    if cover_item is None:
        return None

    content = get_item_bytes(cover_item)
    extension = _cover_extension(cover_item, content)
    if not content or not extension:
        return None

    cover_path = os.path.join(output_dir, SAVED_COVER_BASENAME + extension)
    try:
        with open(cover_path, "wb") as cover_file:
            cover_file.write(content)
        return cover_path
    except Exception:
        return None


def find_saved_epub_cover(txt_dir: str) -> Optional[str]:
    """查找导入 EPUB 时保存的封面图片。"""
    if not txt_dir or not os.path.isdir(txt_dir):
        return None
    for extension in SAVED_COVER_EXTENSIONS:
        cover_path = os.path.join(txt_dir, SAVED_COVER_BASENAME + extension)
        if os.path.isfile(cover_path) and os.path.getsize(cover_path) > 0:
            return cover_path
    return None


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
def _heading_compare_key(text: str) -> str:
    """
    生成仅用于标题比较的文本。

    EPUB 中常混用普通空格、全角空格和不同的标题标点。
    这些排版差异不应导致同一个标题被 TTS 连续朗读两遍。
    """
    normalized = normalize_whitespace(text or "").strip()
    normalized = re.sub(r"\s+", "", normalized)
    normalized = re.sub(r"[:：\-_—–·\(\)（）\[\]【】<>《》\"'“”‘’]+", "", normalized)
    return normalized.casefold()


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

    title_key = _heading_compare_key(norm_title)
    first_paragraph, separator, remaining_text = norm_text.partition("\n\n")
    if separator and title_key and _heading_compare_key(first_paragraph) == title_key:
        return remaining_text.lstrip()

    if norm_text.startswith(norm_title):
        title_end_pos = len(norm_title)
        while title_end_pos < len(norm_text) and norm_text[title_end_pos] in " :：.-_———·\n\r\t":
            title_end_pos += 1

        result = norm_text[title_end_pos:].lstrip()
        if len(result) < len(norm_text) * 0.3:
            return text
        return result

    lines = norm_text.split('\n')
    if len(lines) > 1 and _heading_compare_key(lines[0]) == title_key:
        return '\n'.join(lines[1:]).lstrip()

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
    simp_title = _heading_compare_key(norm_title) if norm_title else ""

    cleaned = []
    prev_s = None

    for i, p in enumerate(paras):
        sp = _heading_compare_key(p)

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
        s = _heading_compare_key(s)
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


# ====== 【函数5】从书籍构建章节 ======
def build_chapters_from_book(book: epub.EpubBook) -> List[Dict[str, Any]]:
    """
    从 EPUB 书籍中构建章节列表
    返回: [{"title": str, "content": str, "hrefs": [str]}, ...]
    """
    href_title_map = build_toc_map(book)
    leaf_toc_map = build_leaf_toc_map(book)
    leaf_toc_entries = build_leaf_toc_entries(book)
    toc_entries_by_file: Dict[str, List[Dict[str, str]]] = {}
    for entry in leaf_toc_entries:
        file_href, _ = _split_toc_href(entry.get("href", ""))
        if file_href:
            toc_entries_by_file.setdefault(file_href, []).append(entry)
    spine = getattr(book, "spine", []) or []
    chapters: List[Dict[str, Any]] = []
    current = None
    pending_structural_headings: List[str] = []

    def should_merge_with_previous(prev_chapter, curr_chapter):
        """判断是否应合并到前一章"""
        # TOC 明确列出的章节即使很短，也是真实章节，不能被启发式规则吞掉。
        if curr_chapter.get("toc_confirmed"):
            return False

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
        nonlocal current, pending_structural_headings
        if current:
            joined = "\n\n".join([t for t in current.get("texts", []) if t])
            content = normalize_whitespace(joined)

            title = (current.get("title") or "").strip()

            # 纯“第一部分”这类结构页，不单独入库，挂到后面的真实章节。
            if title and is_volume_only_title(title) and _count_chars(content) < 1200:
                page_headings = structural_headings_from_page(title, content)
                for heading in list(current.get("structural_headings", []) or []) + page_headings:
                    if heading and heading not in pending_structural_headings:
                        pending_structural_headings.append(heading)
                current = None
                return

            if pending_structural_headings:
                existing_headings = list(current.get("structural_headings", []) or [])
                current["structural_headings"] = pending_structural_headings + [
                    heading for heading in existing_headings
                    if heading not in pending_structural_headings
                ]
                pending_structural_headings = []

            # 在章节正式入库前，统一清理"标题重复"
            if content:
                content = remove_leading_title_from_text(title, content)
                content = remove_redundant_heading_lines(content, title)
                content = dedupe_adjacent_paragraphs(content, title)
                content = normalize_whitespace(content)

            current["content"] = content

            if len(chapters) > 0 and should_merge_with_previous(chapters[-1], current):
                previous = chapters[-1]
                current_content = current.get("content", "")
                current_headings = list(current.get("structural_headings", []) or [])
                if current_headings:
                    current_content = normalize_whitespace(
                        "\n\n".join(current_headings) + "\n\n" + current_content
                    )
                previous["content"] = normalize_whitespace(
                    previous.get("content", "") + "\n\n" + current_content
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

        file_toc_entries = toc_entries_by_file.get(href, [])
        if file_toc_entries:
            audible_entries = [
                entry
                for entry in file_toc_entries
                if not _is_non_audio_toc_title(entry.get("title", ""))
            ]

            # 如果这个文档只包含封面、版权或目录，整份跳过。
            if not audible_entries:
                continue

            toc_segments = extract_toc_anchor_segments(content_bytes, file_toc_entries)
            if toc_segments:
                if current:
                    finalize_current()

                for segment in toc_segments:
                    title = (segment.get("title") or "").strip()
                    if _is_non_audio_toc_title(title):
                        continue

                    current = {
                        "title": title,
                        "texts": [segment.get("content", "")],
                        "hrefs": [segment.get("href", href)],
                        "toc_confirmed": True,
                    }
                    finalize_current()
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
            for heading in structural_headings_from_page(effective_toc_title, text):
                if heading not in pending_structural_headings:
                    pending_structural_headings.append(heading)
            continue

        # 2) 正式 TOC 章节：开新章
        if effective_toc_title:
            if current:
                finalize_current()
            current = {
                "title": effective_toc_title,
                "texts": [],
                "hrefs": [href],
                "toc_confirmed": True,
            }
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

    cover_path = save_epub_cover(book, out_dir)
    if progress_callback and cover_path:
        progress_callback("已提取书籍封面，稍后将写入 MP3...")

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

    def build_txt_header(chapter: Dict[str, Any], chapter_title: str, include_structure: bool = True) -> str:
        """组装 TXT 开头：父级结构标题 -> 真实章节标题。"""
        headings = list(chapter.get("structural_headings", []) or []) if include_structure else []
        headings.append(chapter_title)

        unique_headings: List[str] = []
        seen = set()
        for heading in headings:
            normalized_heading = normalize_whitespace(str(heading or "")).strip()
            key = _heading_compare_key(normalized_heading)
            if normalized_heading and key and key not in seen:
                unique_headings.append(normalized_heading)
                seen.add(key)
        return "\n\n".join(unique_headings)

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
                        f.write(build_txt_header(ch, header, include_structure=(part_num == 1)) + "\n\n")
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
                    f.write(build_txt_header(ch, raw_title) + "\n\n")
                    f.write(content2 if content2 else "(本章无可提取正文)")
                converted_count += 1
                file_counter += 1
            except Exception as e:
                print(f"写入失败：{out_path} -> {e}")

        if progress_callback and idx % 5 == 0:
            progress_callback(f"正在转换: {idx}/{total_chapters}章")

    last_output_dir = out_dir
    return out_dir, converted_count, file_counter - 1
