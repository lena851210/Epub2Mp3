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


# ====== 【函数1】TOC 映射构建 ======
def build_toc_map(book: epub.EpubBook) -> Dict[str, str]:
    """
    构建 EPUB 书籍的目录映射
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
            title = getattr(items, "title", None) or ""
            if href:
                href_key = str(href).split("#")[0]
                href2title[href_key] = title
            subitems = getattr(items, "subitems", None) or getattr(items, "children", None) or getattr(items, "items", None)
            if subitems:
                walk(subitems)
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


# ====== 【函数5】从书籍构建章节 ======
def build_chapters_from_book(book: epub.EpubBook) -> List[Dict[str, Any]]:
    """
    从 EPUB 书籍中构建章节列表
    返回: [{"title": str, "content": str, "hrefs": [str]}, ...]
    """
    href_title_map = build_toc_map(book)
    spine = getattr(book, "spine", []) or []
    chapters: List[Dict[str, Any]] = []
    current = None
    pending_volume_title = ""

    def should_merge_with_previous(prev_chapter, curr_chapter):
        """判断是否应合并到前一章"""
        prev_content = prev_chapter.get("content", "")
        curr_content = curr_chapter.get("content", "")
        if len(curr_content.strip()) < 50 and looks_like_title(curr_content):
            return True
        if len(prev_content.strip()) < 50 and looks_like_body(curr_content):
            return True
        return False

    def looks_like_title(text):
        """判断文本是否像标题"""
        text = text.strip()
        if len(text) < 30 and (text.endswith(('章', '节', '篇')) or not any(c in text for c in '。，；！？')):
            return True
        return False

    def looks_like_body(text):
        """判断文本是否像正文"""
        text = text.strip()
        if len(text) > 100 and any(c in text for c in '。，；！？'):
            return True
        return False

    def finalize_current():
        """完成当前章节处理"""
        nonlocal current, pending_volume_title
        if current:
            joined = "\n\n".join([t for t in current.get("texts", []) if t])
            current["content"] = normalize_whitespace(joined)

            if pending_volume_title and current.get("title"):
                current["title"] = f"{pending_volume_title} {current['title']}".strip()
                pending_volume_title = ""

            if len(chapters) > 0 and should_merge_with_previous(chapters[-1], current):
                previous = chapters[-1]
                previous["content"] = previous.get("content", "") + "\n\n" + current.get("content", "")
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
        text_len = len(text.strip())
        toc_title = href_title_map.get(href, "").strip() if href else ""

        if toc_title and text_len < 120:
            if current:
                finalize_current()
            pending_volume_title = toc_title
            continue

        if toc_title:
            if current:
                finalize_current()
            current = {"title": toc_title, "texts": [], "hrefs": [href]}
            if text:
                current["texts"].append(text)
            continue

        if soup_title and text_len < 300:
            if current and not current.get("texts"):
                current["title"] = soup_title
            else:
                if current:
                    finalize_current()
                current = {"title": soup_title, "texts": [], "hrefs": [href]}
            if text:
                current["texts"].append(text)
            continue

        if not current:
            title_guess = soup_title or (text.splitlines()[0][:50] if text else f"第{len(chapters)+1}章")
            current = {"title": title_guess, "texts": [text] if text else [], "hrefs": [href]}
        else:
            current["texts"].append(text)
            current["hrefs"].append(href)

    if current:
        finalize_current()

    return chapters


# ====== 【函数6】EPUB 转 TXT ======
def convert_epub_to_txt(epub_path: str, progress_callback: Optional[Callable] = None) -> Tuple[str, int, int]:
    """
    将 EPUB 文件转换为 TXT 文件
    
    参数:
        epub_path: EPUB 文件路径
        progress_callback: 进度回调函数 (可选)
    
    返回:
        (输出目录, 转换成功数, 总文件数)
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

    total_chapters = len(chapters)
    converted_count = 0

    # 处理不完整的章节（合并到下一章）
    for idx, ch in enumerate(chapters):
        content = ch.get("content", "").strip()
        if content and not content.endswith(('。', '！', '？', '.', '!', '?')):
            if idx < len(chapters) - 1:
                next_ch = chapters[idx + 1]
                next_content = next_ch.get("content", "").strip()
                next_ch["content"] = content + "\n\n" + next_content
                ch["content"] = ""

    # 写入 TXT 文件
    file_counter = 1
    for idx, ch in enumerate(chapters):
        if not ch.get("content", "").strip():
            continue

        raw_title = ch.get("title") or f"���{file_counter}章"
        safe_title_for_file = sanitize_filename(raw_title, max_len=60)

        content = ch.get("content", "").strip()
        max_chars_per_file = 50000

        # 如果单个章节超过 50000 字符，拆分成多个文件
        if len(content) > max_chars_per_file:
            part_num = 1
            start_pos = 0

            while start_pos < len(content):
                end_pos = start_pos + max_chars_per_file
                if end_pos >= len(content):
                    end_pos = len(content)
                else:
                    # 优先在段落处断开
                    para_end = content.rfind('\n\n', start_pos, end_pos)
                    if para_end != -1 and para_end > start_pos + max_chars_per_file * 0.7:
                        end_pos = para_end + 2
                    else:
                        # 否则在句子处断开
                        sentence_end = max(
                            content.rfind('。', start_pos, end_pos),
                            content.rfind('！', start_pos, end_pos),
                            content.rfind('？', start_pos, end_pos),
                            content.rfind('.', start_pos, end_pos),
                            content.rfind('!', start_pos, end_pos),
                            content.rfind('?', start_pos, end_pos)
                        )
                        if sentence_end != -1 and sentence_end > start_pos + max_chars_per_file * 0.7:
                            end_pos = sentence_end + 1

                part_content = content[start_pos:end_pos].strip()

                if part_num == 1:
                    filename = f"{file_counter:03d}-{safe_title_for_file}.txt"
                else:
                    filename = f"{file_counter:03d}-{safe_title_for_file}_{part_num}.txt"

                out_path = os.path.join(out_dir, filename)

                if part_num == 1:
                    part_content = remove_leading_title_from_text(raw_title, part_content)

                try:
                    with open(out_path, "w", encoding="utf-8") as f:
                        f.write(raw_title + (f" (第{part_num}部分)" if part_num > 1 else "") + "\n\n")
                        f.write(part_content if part_content else "(本章无可提取正文)")
                    converted_count += 1
                except Exception as e:
                    print(f"写入失败：{out_path} -> {e}")

                start_pos = end_pos
                part_num += 1

            file_counter += 1
        else:
            filename = f"{file_counter:03d}-{safe_title_for_file}.txt"
            out_path = os.path.join(out_dir, filename)

            content = remove_leading_title_from_text(raw_title, content)

            try:
                with open(out_path, "w", encoding="utf-8") as f:
                    f.write(raw_title + "\n\n")
                    f.write(content if content else "(本章无可提取正文)")
                converted_count += 1
                file_counter += 1
            except Exception as e:
                print(f"写入失败：{out_path} -> {e}")

        if progress_callback and idx % 5 == 0:
            progress_callback(f"正在转换: {idx}/{total_chapters}章")

    last_output_dir = out_dir
    return out_dir, converted_count, file_counter - 1