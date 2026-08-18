# -*- coding: utf-8 -*-
# 合并脚本：EPUB->TXT + TXT->MP3 一体化


# -*- coding: utf-8 -*-
"""
epub2txt_gui.py
macOS GUI：EPUB -> 按章节 TXT（每章文件名为 001-章节名.txt，首行为章节名）
优化点：
 1. 在HTML解析阶段处理标题重复问题
 2. 避免段落被分割到不同文件
 3. 优化文件命名规则
 4. 智能章节合并
 5. 增强注释过滤功能
"""
import os
import re
import unicodedata
import tkinter as tk
from tkinter import filedialog, messagebox, ttk
import subprocess
from typing import Tuple, Dict, Any, List
import threading
import time

from ebooklib import epub
try:
    from ebooklib import ITEM_DOCUMENT
except Exception:
    ITEM_DOCUMENT = getattr(epub, "ITEM_DOCUMENT", None)

from bs4 import BeautifulSoup, NavigableString, Tag

# 全局记录最近一次输出目录
last_output_dir: str = None

# ====== 配置与常量 ======
BLOCK_TAGS = ("h1","h2","h3","h4","h5","h6","p","li","dd","dt","blockquote","pre","article","section","div")
INLINE_UNWRAP = ("a","span","em","strong","b","i","u","font","mark","small","sub","sup","code","kbd","s")
REMOVE_TAGS = ("script","style","header","footer","nav","aside","figure","figcaption","table","svg","img","video","audio","noscript")
NOISE_KEYWORDS = ("footnote","note","noteref","citation","cite","ref","xref","header","footer","copyright","pagebreak","page-num","toc","index")

# ====== 文本清洗工具 ======
def normalize_whitespace(text: str) -> str:
    text = re.sub(r"[\u00AD\u200B-\u200F\u2028\u2029\uFEFF]", "", text)
    text = text.replace("\xa0", " ")
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\r\n", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()

def safe_node_attrs(node: Any) -> str:
    if not isinstance(node, Tag):
        return ""
    classes = node.get("class", []) or []
    if not isinstance(classes, (list, tuple)):
        classes = [str(classes)]
    id_attr = node.get("id", "") or ""
    epub_type = node.get("epub:type", "") or ""
    parts = []
    if classes:
        parts.append(" ".join([str(x) for x in classes if x]))
    if id_attr:
        parts.append(str(id_attr))
    if epub_type:
        parts.append(str(epub_type))
    return " ".join(parts).lower()

def looks_like_noise(node: Any) -> bool:
    attrs = safe_node_attrs(node)
    return any(k in attrs for k in NOISE_KEYWORDS)

def prepare_soup(html: str) -> BeautifulSoup:
    soup = BeautifulSoup(html, "lxml")
    # 移除不需要的标签
    for t in list(soup.find_all(REMOVE_TAGS)):
        try:
            t.decompose()
        except Exception:
            pass
    
    # 移除注释和脚注
    for t in list(soup.find_all(True)):
        try:
            if looks_like_noise(t):
                t.decompose()
            # 额外检查：移除包含注释标记的元素
            elif t.get('role') in ['note', 'comment', 'footnote']:
                t.decompose()
            # 移除常见的注释类名
            elif any(keyword in (t.get('class') or []) for keyword in ['note', 'footnote', 'annotation', 'comment']):
                t.decompose()
        except Exception:
            continue
    
    # 处理行内标签
    for br in soup.find_all("br"):
        try:
            br.replace_with(NavigableString("\n"))
        except Exception:
            pass
    
    # 解包行内标签
    for tagname in INLINE_UNWRAP:
        for t in list(soup.find_all(tagname)):
            try:
                t.unwrap()
            except Exception:
                pass
    
    # 移除注释引用（如【1】、[1]等）
    text_content = str(soup)
    text_content = re.sub(r'【\d+】|\[\d+\]|\(\d+\)|<\d+>', '', text_content)
    soup = BeautifulSoup(text_content, "lxml")
    
    return soup

def extract_title_from_soup(soup: BeautifulSoup) -> str:
    # 优先从标题标签提取
    for tag in ("h1","h2","h3","h4","h5","h6"):
        el = soup.find(tag)
        if el:
            # 获取标题文本，但跳过数字部分（如果有）
            title_text = " ".join(el.stripped_strings)
            if title_text:
                # 移除可能重复的数字前缀
                title_text = re.sub(r'^\d+\s*', '', title_text).strip()
                if title_text:
                    return title_text
    
    # 其次从title标签提取
    title_el = soup.find("title")
    if title_el:
        title_text = " ".join(title_el.stripped_strings)
        if title_text:
            # 移除可能重复的数字前缀
            title_text = re.sub(r'^\d+\s*', '', title_text).strip()
            return title_text
    
    return ""

def text_of(el: Tag) -> str:
    try:
        return " ".join(s.strip() for s in el.stripped_strings)
    except Exception:
        try:
            return "".join([str(x) for x in el.stripped_strings])
        except Exception:
            return ""

def soup_to_paragraphs(soup: BeautifulSoup) -> str:
    body = soup.body if soup.body else soup
    
    # 先移除所有标题标签，避免标题重复
    for tag in ("h1","h2","h3","h4","h5","h6"):
        for h in body.find_all(tag):
            h.decompose()
    
    paras: List[str] = []
    for blk in list(body.find_all(BLOCK_TAGS)):
        try:
            if any(parent.name in BLOCK_TAGS for parent in getattr(blk, "parents", [])):
                continue
        except Exception:
            pass
        t = text_of(blk)
        if t:
            # 过滤掉注释内容
            if not re.search(r'注释|注\d+|footnote|note|注解', t, re.IGNORECASE):
                paras.append(t)
    if len(paras) <= 1:
        whole = " ".join(s.strip() for s in body.stripped_strings) if hasattr(body, "stripped_strings") else ""
        if whole:
            parts = re.split(r"(?<=[。！？\.\?\!])\s+|\n{2,}|\r\n", whole)
            parts = [p.strip() for p in parts if p.strip()]
            if parts:
                paras = [p for p in parts if not re.search(r'注释|注\d+|footnote|note|注解', p, re.IGNORECASE)]
    txt = "\n\n".join(paras)
    txt = normalize_whitespace(txt)
    return txt

def merge_broken_short_lines(text: str) -> str:
    lines = [ln.rstrip() for ln in text.splitlines()]
    merged = []
    buf = []
    for ln in lines:
        s = ln.strip()
        if not s:
            if buf:
                if len(buf) >= 3:
                    merged.append("".join(buf))
                else:
                    merged.extend(buf)
                buf = []
            merged.append("")
            continue
        short_line = len(s) <= 3
        if short_line:
            buf.append(s)
        else:
            if buf:
                if len(buf) >= 3:
                    merged.append("".join(buf))
                else:
                    merged.extend(buf)
                buf = []
            merged.append(s)
    if buf:
        if len(buf) >= 3:
            merged.append("".join(buf))
        else:
            merged.extend(buf)

    paragraphs = []
    para_buf = []
    for ln in merged:
        if not ln:
            if para_buf:
                paragraphs.append(" ".join(para_buf))
                para_buf = []
        else:
            para_buf.append(ln)
    if para_buf:
        paragraphs.append(" ".join(para_buf))

    result = "\n\n".join([normalize_whitespace(p) for p in paragraphs])
    return normalize_whitespace(result)

def clean_text_from_html_bytes(html_bytes: bytes) -> Tuple[str, str]:
    html_str = html_bytes.decode("utf-8", errors="ignore")
    soup = prepare_soup(html_str)
    title = extract_title_from_soup(soup)
    text = soup_to_paragraphs(soup)
    text = merge_broken_short_lines(text)
    text = normalize_whitespace(text)
    return title, text

# ====== 文件名和 TOC 映射 ======
def sanitize_filename(name: str, max_len: int = 80) -> str:
    if not name:
        return "无标题章节"
    name = unicodedata.normalize("NFKC", str(name)).strip()
    # 移除特殊字符，保留中文、英文、数字和下划线
    name = re.sub(r'[\\/:*?"<>|\n\r\t]+', "", name)
    name = re.sub(r"\s+", " ", name)
    if len(name) > max_len:
        name = name[:max_len].rstrip()
    return name or "无标题章节"

def build_toc_map(book: epub.EpubBook) -> Dict[str, str]:
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

# ====== 读取 item 内容的通用方法 ======
def get_item_bytes(item) -> bytes:
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

def get_item_href_key(item) -> str:
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

# ====== 将 spine 分组为章节（合并分散 xhtml） ======
def build_chapters_from_book(book: epub.EpubBook) -> List[Dict[str, Any]]:
    href_title_map = build_toc_map(book)
    spine = getattr(book, "spine", []) or []
    chapters: List[Dict[str, Any]] = []
    current = None
    idx_est = 0

    def finalize_current():
        nonlocal current
        if current:
            # 合并 texts 列表成正文
            joined = "\n\n".join([t for t in current.get("texts", []) if t])
            current["content"] = normalize_whitespace(joined)
            
            # 检查是否需要合并到前一章（如果当前章节内容过少且可能是标题页）
            if len(chapters) > 0 and should_merge_with_previous(chapters[-1], current):
                # 合并到前一章
                previous = chapters[-1]
                previous["content"] = previous.get("content", "") + "\n\n" + current.get("content", "")
                previous["hrefs"].extend(current.get("hrefs", []))
                # 更新前一章的标题（如果有更具体的标题）
                if current.get("title") and len(current["title"]) > len(previous.get("title", "")):
                    previous["title"] = current["title"]
            else:
                # 作为独立章节添加
                chapters.append(current)
            current = None

    def should_merge_with_previous(prev_chapter, curr_chapter):
        """判断当前章节是否应该合并到前一章"""
        prev_content = prev_chapter.get("content", "")
        curr_content = curr_chapter.get("content", "")
        
        # 如果当前章节内容很少（少于50字符）且看起来像标题
        if len(curr_content.strip()) < 50 and looks_like_title(curr_content):
            return True
            
        # 如果前一章内容很少且当前章节看起来是正文
        if len(prev_content.strip()) < 50 and looks_like_body(curr_content):
            return True
            
        return False

    def looks_like_title(text):
        """判断文本是否看起来像标题"""
        text = text.strip()
        # 标题通常较短，不含标点或标点较少
        if len(text) < 30 and (text.endswith(('章', '节', '篇')) or not any(c in text for c in '。，；！？')):
            return True
        return False

    def looks_like_body(text):
        """判断文本是否看起来像正文"""
        text = text.strip()
        # 正文通常较长，包含标点
        if len(text) > 100 and any(c in text for c in '。，；！？'):
            return True
        return False

    # 获取所有文档项
    all_items = []
    for item_id, linear in spine:
        try:
            item = book.get_item_with_id(item_id)
            if item is None:
                continue
            # 检查是否为文档类型
            if ITEM_DOCUMENT is not None:
                try:
                    if item.get_type() != ITEM_DOCUMENT:
                        continue
                except Exception:
                    pass
            all_items.append(item)
        except Exception:
            continue

    # 处理所有文档项
    for item in all_items:
        idx_est += 1
        href = get_item_href_key(item)
        content_bytes = get_item_bytes(item)
        if not content_bytes:
            continue
            
        soup_title, text = clean_text_from_html_bytes(content_bytes)
        text_len = len(text.strip())
        toc_title = href_title_map.get(href, "").strip() if href else ""

        has_heading = bool(soup_title.strip())
        is_short = text_len < 200

        # 决策逻辑：
        if toc_title:
            # TOC 明确定义为一个新章的开始 -> finalize 前一章
            if current and (current.get("texts") or current.get("title")):
                finalize_current()
            current = {"title": toc_title or None, "texts": [], "hrefs":[href]}
            if text:
                current["texts"].append(text)
            continue

        if has_heading and is_short:
            # 很可能是标题页（短） -> 如果当前为空则开始新章并等待后续正文合并
            if current and (not current.get("texts")) and (not current.get("title")):
                current["title"] = soup_title
                current["hrefs"].append(href)
            else:
                # 若 current 有正文，则认为这是一段独立短标题（也把它并入当前），否则开始新章
                if current and current.get("texts"):
                    current["texts"].append(text)
                    current["hrefs"].append(href)
                else:
                    # 新开始一章，但不要 finalize（等待正文）
                    if current and (current.get("title") or current.get("texts")):
                        finalize_current()
                    current = {"title": soup_title or None, "texts": [], "hrefs":[href]}
            continue

        # 其他情况：正文（或中等长度内容）
        if not current:
            title_guess = soup_title or (text.splitlines()[0][:60] if text else f"第{len(chapters)+1}章")
            current = {"title": title_guess, "texts":[text] if text else [], "hrefs":[href]}
        else:
            current["texts"].append(text)
            current["hrefs"].append(href)

    # 结束循环，收尾
    if current:
        finalize_current()

    return chapters

# ====== 处理重复标题 —— 若正文起始包含标题则去掉重复部分 ======
def remove_leading_title_from_text(title: str, text: str) -> str:
    if not title or not text:
        return text
        
    # 标准化标题和文本
    norm_title = normalize_whitespace(title).strip(" \n\r\t")
    norm_text = normalize_whitespace(text)
    
    # 如果文本为空或标题为空，直接返回
    if not norm_title or not norm_text:
        return text
    
    # 情况1: 文本完全以标题开头（可能带有标点或空格）
    if norm_text.startswith(norm_title):
        # 计算标题后的位置
        title_end_pos = len(norm_title)
        # 跳过可能的标点符号和空格
        while title_end_pos < len(norm_text) and norm_text[title_end_pos] in " :：.-_———·\n\r\t":
            title_end_pos += 1
            
        # 返回标题后的内容
        result = norm_text[title_end_pos:].lstrip()
        # 如果移除后内容太少，可能误删，恢复原文本
        if len(result) < len(norm_text) * 0.3:
            return text
        return result
    
    # 情况2: 标题作为单独一行出现在文本开头
    lines = norm_text.split('\n')
    if len(lines) > 1 and normalize_whitespace(lines[0]) == norm_title:
        # 移除第一行
        result = '\n'.join(lines[1:]).lstrip()
        if len(result) < len(norm_text) * 0.3:
            return text
        return result
        
    # 情况3: 标题作为单独一行出现在文本开头（可能有数字前缀）
    if len(lines) > 1:
        first_line_clean = re.sub(r'^\d+\s*', '', normalize_whitespace(lines[0]))  # 移除行首数字
        if first_line_clean == norm_title:
            # 移除第一行
            result = '\n'.join(lines[1:]).lstrip()
            if len(result) < len(norm_text) * 0.3:
                return text
            return result
    
    # 情况4: 标题作为段落开头（后面紧跟换行）
    pattern = r'^\s*' + re.escape(norm_title) + r'[\s\:\-–—…\._]*\n+'
    new_text = re.sub(pattern, "", norm_text, flags=re.IGNORECASE)
    
    # 情况5: 处理HTML结构化标题重复（如数字和标题分行显示）
    if re.search(rf'^\d+\s*\n+{re.escape(norm_title)}', norm_text):
        new_text = re.sub(rf'^\d+\s*\n+{re.escape(norm_title)}\s*\n+', '', norm_text)
    
    # 如果移除后文本变短很多，可能误删了，恢复原文本
    if len(new_text) < len(norm_text) * 0.7:
        return text
        
    return new_text

# ====== 主转换函数 ======
def convert_epub_to_txt(epub_path: str, progress_callback=None) -> str:
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
        raise RuntimeError("未能从 EPUB 中提取到任何章节。请确认 EPUB 是否受保护或结构特殊。")

    total_chapters = len(chapters)
    converted_count = 0
    
    # 第一遍：检查所有章节内容，确保没有段落被分割
    for idx, ch in enumerate(chapters):
        content = ch.get("content", "").strip()
        # 检查章节结尾是否是不完整的句子
        if content and not content.endswith(('。', '！', '？', '.', '!', '?')):
            # 如果不完整，尝试合并到下一章
            if idx < len(chapters) - 1:
                next_ch = chapters[idx + 1]
                next_content = next_ch.get("content", "").strip()
                # 将当前章节内容添加到下一章开头
                next_ch["content"] = content + "\n\n" + next_content
                # 标记当前章节为空
                ch["content"] = ""
    
    # 第二遍：写入文件
    file_counter = 1
    for idx, ch in enumerate(chapters):
        if not ch.get("content", "").strip():
            continue  # 跳过空章节（已被合并）
            
        raw_title = ch.get("title") or f"第{file_counter}章"
        safe_title_for_file = sanitize_filename(raw_title, max_len=60)
        
        # 检查内容是否过长需要分割
        content = ch.get("content", "").strip()
        max_chars_per_file = 50000  # 每个文件最多5万字
        
        if len(content) > max_chars_per_file:
            # 需要分割文件
            part_num = 1
            start_pos = 0
            
            while start_pos < len(content):
                # 找到合适的分割点（段落结束处）
                end_pos = start_pos + max_chars_per_file
                if end_pos >= len(content):
                    end_pos = len(content)
                else:
                    # 向后查找段落结束位置
                    para_end = content.rfind('\n\n', start_pos, end_pos)
                    if para_end != -1 and para_end > start_pos + max_chars_per_file * 0.7:
                        end_pos = para_end + 2  # 包括两个换行符
                    else:
                        # 找不到段落结束，找句子结束
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
                
                # 提取分段内容
                part_content = content[start_pos:end_pos].strip()
                
                # 准备文件名
                if part_num == 1:
                    filename = f"{file_counter:03d}-{safe_title_for_file}.txt"
                else:
                    filename = f"{file_counter:03d}-{safe_title_for_file}_{part_num}.txt"
                
                out_path = os.path.join(out_dir, filename)
                
                # 准备正文：去除可能与标题重复的开头（仅对第一部分）
                if part_num == 1:
                    part_content = remove_leading_title_from_text(raw_title, part_content)
                
                # 写入文件
                try:
                    with open(out_path, "w", encoding="utf-8") as f:
                        f.write(raw_title + (f" (第{part_num}部分)" if part_num > 1 else "") + "\n\n")
                        f.write(part_content if part_content else "(本章无可提取正文)")
                    converted_count += 1
                except Exception as e:
                    print(f"写入失败：{out_path} -> {e}")
                
                # 更新位置和部分计数
                start_pos = end_pos
                part_num += 1
            
            file_counter += 1
        else:
            # 不需要分割
            filename = f"{file_counter:03d}-{safe_title_for_file}.txt"
            out_path = os.path.join(out_dir, filename)
            
            # 准备正文：去除可能与标题重复的开头
            content = remove_leading_title_from_text(raw_title, content)
            
            # 写入文件
            try:
                with open(out_path, "w", encoding="utf-8") as f:
                    f.write(raw_title + "\n\n")
                    f.write(content if content else "(本章无可提取正文)")
                converted_count += 1
                file_counter += 1
            except Exception as e:
                print(f"写入失败：{out_path} -> {e}")
            
        if progress_callback and idx % 5 == 0:  # 每5章更新一次进度
            progress_callback(f"正在转换: {idx}/{total_chapters}章")

    last_output_dir = out_dir
    return out_dir, converted_count, file_counter - 1  # file_counter - 1 是实际文件数



# ====== 以上为 EPUB->TXT 核心函数（保留原算法与逻辑） ======

import os
import json
import time
import asyncio
import edge_tts
import tkinter as tk
from tkinter import filedialog, messagebox, ttk
import threading
import tempfile
import platform
import subprocess
from pydub import AudioSegment
import shutil
import re

CONFIG_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "config.json")
BASE_WORDS_PER_MINUTE = 300  # 基准：1.0x 约 300 字/分钟（中文按“字”计）

VOICE_MAPPING = {
    "晓晓(女)": "zh-CN-XiaoxiaoNeural",
    "晓伊(女)": "zh-CN-XiaoyiNeural",
    "云健(男)": "zh-CN-YunjianNeural",
    "云希(男)": "zh-CN-YunxiNeural",
    "云夏(男)": "zh-CN-YunxiaNeural",
    "云扬(男)": "zh-CN-YunyangNeural",
    "晓北(辽宁,女)": "zh-CN-liaoning-XiaobeiNeural",
    "晓妮(陕西,女)": "zh-CN-shaanxi-XiaoniNeural",
    "云皓(男)": "zh-CN-YunhaoNeural",
    "晓萱(女)": "zh-CN-XiaoxuanNeural",
    "云枫(男)": "zh-CN-YunfengNeural",
    "晓梦(女)": "zh-CN-XiaomengNeural"
}

def load_config():
    default_config = {
        "edge": {"voice_name": "zh-CN-XiaoxiaoNeural", "speed": 1.0, "pitch": 0, "volume": 0},
        "last_txt_dir": "",
        "merge_audio": True,
        "target_duration": 40,
        "words_per_minute": BASE_WORDS_PER_MINUTE
    }
    if os.path.exists(CONFIG_FILE):
        with open(CONFIG_FILE, "r", encoding="utf-8") as f:
            cfg = json.load(f)
            if "edge" not in cfg: cfg["edge"] = {}
            for k, v in default_config["edge"].items():
                if k not in cfg["edge"]:
                    cfg["edge"][k] = v
            for k, v in default_config.items():
                if k not in cfg:
                    cfg[k] = v
            return {**default_config, **cfg}
    else:
        with open(CONFIG_FILE, "w", encoding="utf-8") as f:
            json.dump(default_config, f, ensure_ascii=False, indent=2)
        return default_config

def save_config(cfg):
    with open(CONFIG_FILE, "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)

def preprocess_text(text, max_length=500):
    if len(text) <= max_length:
        return [text]
    paras, current = [], ""
    for sentence in text.replace("！", "！\n").replace("？", "？\n").replace("。", "。\n").split("\n"):
        if not sentence.strip():
            continue
        test = current + sentence
        if len(test) <= max_length:
            current = test
        else:
            if current:
                paras.append(current)
            current = sentence
    if current:
        paras.append(current)
    return paras

class EdgeTTSWrapper:
    def __init__(self):
        self.voices = []
        self._load_voices_blocking()
        threading.Thread(target=self._load_voices_async, daemon=True).start()

    def _load_voices_blocking(self):
        try:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            _ = loop.run_until_complete(edge_tts.list_voices())
            self.voices = list(VOICE_MAPPING.keys())
            loop.close()
        except Exception as e:
            print("获取声音列表失败:", e)
            self.voices = list(VOICE_MAPPING.keys())

    def _load_voices_async(self):
        try:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            _ = loop.run_until_complete(edge_tts.list_voices())
            self.voices = list(VOICE_MAPPING.keys())
        except Exception as e:
            print("异步加载声音失败:", e)
            self.voices = list(VOICE_MAPPING.keys())

    def refresh_voices(self):
        self._load_voices_blocking()
        return self.voices

    def text_to_speech(self, text, voice, speed, pitch, volume, output_file):
        edge_voice = VOICE_MAPPING.get(voice, "zh-CN-XiaoxiaoNeural")
        async def _synth():
            communicate = edge_tts.Communicate(
                text, edge_voice,
                rate=f"{(speed-1)*100:+.0f}%",
                pitch=f"{pitch:+.0f}Hz",
                volume=f"{volume:+.0f}%"
            )
            await communicate.save(output_file)
        loop = asyncio.new_event_loop()
        try:
            loop.run_until_complete(_synth())
        finally:
            loop.close()

class AudiobookGenerator:
    def __init__(self):
        self.config = load_config()
        self.edge = EdgeTTSWrapper()
        self.root = tk.Tk()
        self.root.title("有声书生成工具 (Edge TTS)")
        self.root.geometry("960x680")
        self.root.minsize(720, 560)
        self.stop_flag = False

        # 覆盖控件与状态
        self.selection_states = {}   # iid -> bool
        self.selection_vars = {}     # iid -> BooleanVar
        self.tree_checks = {}        # iid -> ttk.Checkbutton
        self.progress_vars = {}      # iid -> DoubleVar
        self.tree_progress = {}      # iid -> ttk.Progressbar

        # 文件字数缓存（用于估算时长）
        self.file_chars = {}         # iid -> int(去空白字数)

        # 错误详情（双击状态查看）
        self.error_detail = {}       # iid -> str

        # 状态列旋转指示器
        self.spinner_frames = ["⠋","⠙","⠹","⠸","⠼","⠴","⠦","⠧","⠇","⠏"]
        self.spinner_active = {}     # iid -> {"base": str, "idx": int}
        self.spinner_job = None

        # 自适应列宽防抖
        self._resize_job = None

        self.create_ui()

    # ============ UI ============

    def create_ui(self):
        style = ttk.Style(self.root)
        sysname = platform.system()
        try:
            if sysname == "Windows":
                style.theme_use("vista")
            elif sysname == "Darwin":
                style.theme_use("aqua")
            else:
                style.theme_use("clam")
        except Exception:
            pass

        BASE_FONT = ("Helvetica" if sysname == "Darwin" else "Segoe UI", 11)
        style.configure(".", font=BASE_FONT)
        style.configure("Treeview.Heading", font=(BASE_FONT[0], BASE_FONT[1], "bold"))
        style.configure("Treeview", rowheight=28)

        main = ttk.Frame(self.root, padding=(12, 10, 12, 10))
        main.grid(row=0, column=0, sticky="nsew")
        self.root.columnconfigure(0, weight=1)
        self.root.rowconfigure(0, weight=1)
        main.rowconfigure(2, weight=1)  # 文件列表伸缩
        main.columnconfigure(0, weight=1)

        # 语音设置
        voice_lf = ttk.LabelFrame(main, text="语音设置", padding=(10, 8))
        voice_lf.grid(row=0, column=0, sticky="ew", padx=0, pady=(0, 8))
        voice_lf.columnconfigure(1, weight=1)

        ttk.Label(voice_lf, text="音色:").grid(row=0, column=0, padx=(0, 8), pady=(0, 6), sticky="w")
        current_voice_display = next((d for d, e in VOICE_MAPPING.items() if e == self.config["edge"]["voice_name"]), "晓晓(女)")
        self.voice_var = tk.StringVar(value=current_voice_display)
        self.voice_combo = ttk.Combobox(voice_lf, textvariable=self.voice_var, values=self.edge.voices, state="readonly")
        self.voice_combo.grid(row=0, column=1, sticky="ew", padx=(0, 8), pady=(0, 6))
        ttk.Button(voice_lf, text="刷新列表", command=self.refresh_voices, width=10).grid(row=0, column=2, padx=(0, 6), pady=(0, 6), sticky="e")
        ttk.Button(voice_lf, text="试听", command=self.preview_audio, width=8).grid(row=0, column=3, padx=(0, 0), pady=(0, 6), sticky="e")

        sliders = ttk.Frame(voice_lf)
        sliders.grid(row=1, column=0, columnspan=4, sticky="ew", pady=(4, 0))
        for i in range(3):
            sliders.columnconfigure(i, weight=1)

        def make_slider(parent, label_text, var, from_, to, fmt):
            frame = ttk.Frame(parent)
            label_var = tk.StringVar(value=f"{label_text}: {fmt.format(var.get())}")
            ttk.Label(frame, textvariable=label_var, anchor="w").pack(fill="x")
            scale = ttk.Scale(frame, from_=from_, to=to, variable=var, orient="horizontal")
            scale.pack(fill="x")
            var.trace_add("write", lambda *args: label_var.set(f"{label_text}: {fmt.format(var.get())}"))
            return frame

        self.speed_var = tk.DoubleVar(value=self.config["edge"]["speed"])
        speed_frame = make_slider(sliders, "语速", self.speed_var, 0.5, 2.0, "{:.2f}x")
        speed_frame.grid(row=0, column=0, sticky="ew", padx=(0, 10))

        self.pitch_var = tk.DoubleVar(value=self.config["edge"]["pitch"])
        make_slider(sliders, "音调", self.pitch_var, -50, 50, "{:+.0f}Hz").grid(row=0, column=1, sticky="ew", padx=(0, 10))
        self.volume_var = tk.DoubleVar(value=self.config["edge"]["volume"])
        make_slider(sliders, "音量", self.volume_var, -100, 100, "{:+.0f}%").grid(row=0, column=2, sticky="ew")

        def update_words_per_minute(*args):
            estimated_wpm = max(1, int(BASE_WORDS_PER_MINUTE * self.speed_var.get()))
            # 估算字数（字/分钟）
            self.wpm_var.set(estimated_wpm)
            self.wpm_label_var.set(f"估算字数: {estimated_wpm} 字/分钟")
            # 同步更新“预估时长”列
            self.update_all_estimates()
        self.wpm_var = tk.IntVar(value=self.config.get("words_per_minute", BASE_WORDS_PER_MINUTE))
        self.wpm_label_var = tk.StringVar(value=f"估算字数: {self.wpm_var.get()} 字/分钟")
        self.speed_var.trace_add("write", update_words_per_minute)

        # 输出设置
        out_lf = ttk.LabelFrame(main, text="输出设置", padding=(10, 8))
        out_lf.grid(row=1, column=0, sticky="ew", padx=0, pady=(0, 8))
        out_lf.columnconfigure(1, weight=1)

        self.merge_var = tk.BooleanVar(value=self.config.get("merge_audio", True))
        merge_row = ttk.Frame(out_lf)
        merge_row.grid(row=0, column=0, columnspan=4, sticky="ew", pady=(0, 6))
        ttk.Checkbutton(merge_row, text="合并音频为长段落", variable=self.merge_var, command=self.toggle_merge_options).pack(side="left")
        ttk.Label(merge_row, text="目标时长(分钟):").pack(side="left", padx=(10, 6))
        self.target_duration_var = tk.IntVar(value=self.config.get("target_duration", 40))
        self.target_duration_spin = ttk.Spinbox(merge_row, from_=10, to=120, width=6, textvariable=self.target_duration_var)
        self.target_duration_spin.pack(side="left", padx=(0, 12))
        ttk.Label(merge_row, textvariable=self.wpm_label_var).pack(side="left")

        dir_row = ttk.Frame(out_lf)
        dir_row.grid(row=1, column=0, columnspan=4, sticky="ew")
        dir_row.columnconfigure(1, weight=1)
        ttk.Label(dir_row, text="TXT目录:").grid(row=0, column=0, padx=(0, 8), sticky="w")
        self.txt_dir = tk.StringVar(value=self.config.get("last_txt_dir", ""))
        ttk.Entry(dir_row, textvariable=self.txt_dir).grid(row=0, column=1, sticky="ew", padx=(0, 8))
        ttk.Button(dir_row, text="浏览...", command=self.select_input_dir, width=8).grid(row=0, column=2, sticky="e")

        # 源文件（可伸缩区）
        files_lf = ttk.LabelFrame(main, text="源文件", padding=(10, 8))
        files_lf.grid(row=2, column=0, sticky="nsew", padx=0, pady=(0, 8))
        files_lf.columnconfigure(0, weight=1)
        files_lf.rowconfigure(2, weight=1)  # Treeview 区域伸缩

        topbar = ttk.Frame(files_lf)
        topbar.grid(row=0, column=0, sticky="ew", pady=(0, 6))
        topbar.columnconfigure(0, weight=1)
        left_group = ttk.Frame(topbar)
        left_group.grid(row=0, column=0, sticky="w")
        ttk.Label(left_group, text="TXT列表（双击名称可预览）").pack(side="left", padx=(0, 6))
        ttk.Button(left_group, text="【全选】", command=self.select_all_files, style="Toolbutton").pack(side="left", padx=(0, 4))
        ttk.Button(left_group, text="【全不选】", command=self.unselect_all_files, style="Toolbutton").pack(side="left", padx=(0, 4))
        ttk.Button(left_group, text="【反选】", command=self.invert_selection, style="Toolbutton").pack(side="left", padx=(0, 4))
        self.files_info_var = tk.StringVar(value="当前目录未加载")
        ttk.Label(topbar, textvariable=self.files_info_var, foreground="#666").grid(row=0, column=1, sticky="e")

        # 表格 + 双滚动条（新增“预估时长”列）
        columns = ("select", "name", "size", "chars", "est", "status", "progress")
        self.files_tree = ttk.Treeview(files_lf, columns=columns, show="headings")
        self.files_tree.heading("select", text="选择")
        self.files_tree.heading("name", text="TXT名称")
        self.files_tree.heading("size", text="大小(KB)")
        self.files_tree.heading("chars", text="字数")
        self.files_tree.heading("est", text="预估时长")
        self.files_tree.heading("status", text="状态")
        self.files_tree.heading("progress", text="进度")

        # 初始列宽（后续自适应）
        self.files_tree.column("select", width=48, minwidth=36, anchor="center", stretch=False)
        self.files_tree.column("name", width=420, minwidth=200, anchor="w", stretch=True)
        self.files_tree.column("size", width=90, minwidth=72, anchor="e", stretch=False)
        self.files_tree.column("chars", width=90, minwidth=72, anchor="e", stretch=False)
        self.files_tree.column("est", width=100, minwidth=80, anchor="center", stretch=False)
        self.files_tree.column("status", width=220, minwidth=160, anchor="w", stretch=True)
        self.files_tree.column("progress", width=180, minwidth=120, anchor="center", stretch=True)

        vsb = ttk.Scrollbar(files_lf, orient="vertical", command=self.files_tree.yview)
        hsb = ttk.Scrollbar(files_lf, orient="horizontal", command=self.files_tree.xview)
        self.files_tree.configure(yscrollcommand=vsb.set, xscrollcommand=hsb.set)
        self.files_tree.grid(row=2, column=0, sticky="nsew")
        vsb.grid(row=2, column=1, sticky="ns")
        hsb.grid(row=3, column=0, sticky="ew")

        # 事件
        self.files_tree.bind("<Configure>", lambda e: self._on_tree_configure())
        self.files_tree.bind("<MouseWheel>", lambda e: self.root.after_idle(self.refresh_tree_overlays))
        self.files_tree.bind("<Button-4>", lambda e: self.root.after_idle(self.refresh_tree_overlays))
        self.files_tree.bind("<Button-5>", lambda e: self.root.after_idle(self.refresh_tree_overlays))
        self.files_tree.bind("<Double-1>", self.on_tree_double_click)
        self.root.bind("<Configure>", lambda e: self._schedule_adjust_columns())

        # 底部按钮（固定右下）
        button_row = ttk.Frame(main)
        button_row.grid(row=3, column=0, sticky="ew", pady=(0, 6))
        button_row.columnconfigure(1, weight=1)
        left_btns = ttk.Frame(button_row)
        left_btns.grid(row=0, column=0, sticky="w")
        ttk.Button(left_btns, text="停止", command=self.stop_generation, width=10).pack(side="left")
        right_btns = ttk.Frame(button_row)
        right_btns.grid(row=0, column=2, sticky="e")
        ttk.Button(right_btns, text="导入 EPUB→TXT", command=self.import_epub, width=16).pack(side="left", padx=(0, 8))

        ttk.Button(right_btns, text="打开音频目录 📁", command=self.open_output_dir, width=16).pack(side="left", padx=(0, 8))
        ttk.Button(right_btns, text="开始转换 🚀", command=self.start_generation, width=16).pack(side="left")

        # 状态栏
        status_bar = ttk.Frame(main)
        status_bar.grid(row=4, column=0, sticky="ew")
        self.status_var = tk.StringVar(value="就绪")
        ttk.Label(status_bar, textvariable=self.status_var, anchor="w").pack(fill="x")

        # 初始化/载入
        self.root.protocol("WM_DELETE_WINDOW", self.on_closing)
        if self.txt_dir.get() and os.path.isdir(self.txt_dir.get()):
            self.load_file_list(self.txt_dir.get())
        self._warn_if_no_ffmpeg()
        self.root.after(50, self.adjust_column_widths)
        # 首次也刷新预估时长
        self.update_all_estimates()

    # ============ 小工具与格式化 ============

    def _has_ffmpeg(self):
        return shutil.which("ffmpeg") is not None

    def _warn_if_no_ffmpeg(self):
        if not self._has_ffmpeg():
            self.set_status("未检测到 ffmpeg，合并/导出可能失败。macOS 可执行: brew install ffmpeg")

    def set_status(self, text):
        self.root.after(0, lambda: self.status_var.set(text))

    def _on_tree_configure(self):
        self.refresh_tree_overlays()
        self._schedule_adjust_columns()

    def _schedule_adjust_columns(self):
        if self._resize_job:
            try: self.root.after_cancel(self._resize_job)
            except Exception: pass
        self._resize_job = self.root.after(120, self.adjust_column_widths)

    def adjust_column_widths(self):
        self._resize_job = None
        w = self.files_tree.winfo_width()
        if w <= 50:
            return
        scrollbar_w = 16
        total_avail = max(200, w - scrollbar_w)

        # 固定列：选择/大小/字数/预估时长
        sel_w = 48
        size_w = 90
        chars_w = 90
        est_w = 100
        fixed_total = sel_w + size_w + chars_w + est_w

        var_avail = max(120, total_avail - fixed_total)
        name_min, status_min, prog_min = 200, 160, 120
        name_weight, status_weight, prog_weight = 0.58, 0.26, 0.16

        base_need = name_min + status_min + prog_min
        if var_avail <= base_need:
            name_w, status_w, progress_w = name_min, status_min, prog_min
        else:
            name_w = max(name_min, int(var_avail * name_weight))
            status_w = max(status_min, int(var_avail * status_weight))
            progress_w = max(prog_min, var_avail - name_w - status_w)

        self.files_tree.column("select", width=sel_w, minwidth=36, stretch=False)
        self.files_tree.column("size", width=size_w, minwidth=72, stretch=False)
        self.files_tree.column("chars", width=chars_w, minwidth=72, stretch=False)
        self.files_tree.column("est", width=est_w, minwidth=80, stretch=False)
        self.files_tree.column("name", width=name_w, minwidth=name_min, stretch=True)
        self.files_tree.column("status", width=status_w, minwidth=status_min, stretch=True)
        self.files_tree.column("progress", width=progress_w, minwidth=prog_min, stretch=True)

        self.refresh_tree_overlays()

    def seconds_to_str(self, total_seconds: int) -> str:
        if total_seconds < 3600:
            m = total_seconds // 60
            s = total_seconds % 60
            return f"{m}:{s:02d}"
        else:
            h = total_seconds // 3600
            m = (total_seconds % 3600) // 60
            s = total_seconds % 60
            return f"{h}:{m:02d}:{s:02d}"

    def estimate_duration_str(self, chars: int) -> str:
        wpm = max(1, self.wpm_var.get())
        seconds = int(round(chars / wpm * 60))
        return self.seconds_to_str(seconds)

    def update_all_estimates(self):
        # 根据当前WPM刷新“预估时长”列
        try:
            for iid in self.files_tree.get_children():
                chars = self.file_chars.get(iid, None)
                if chars is None:
                    # 从“字数”列读一次
                    try:
                        chars = int(self.files_tree.set(iid, "chars"))
                        self.file_chars[iid] = chars
                    except Exception:
                        continue
                self.files_tree.set(iid, "est", self.estimate_duration_str(chars))
        except Exception:
            pass

    def get_mp3_duration_str(self, path: str) -> str | None:
        try:
            audio = AudioSegment.from_file(path, format="mp3")
            ms = len(audio)
            return self.seconds_to_str(int(round(ms / 1000)))
        except Exception:
            return None

    def refresh_voices(self):
        def task():
            self.set_status("正在刷新音色列表...")
            voices = self.edge.refresh_voices()
            self.root.after(0, lambda: self.voice_combo.configure(values=voices))
            self.set_status(f"音色列表已刷新，共 {len(voices)} 个")
        threading.Thread(target=task, daemon=True).start()

    def toggle_merge_options(self):
        state = "normal" if self.merge_var.get() else "disabled"
        self.target_duration_spin.configure(state=state)

    def select_input_dir(self):
        last_dir = self.txt_dir.get()
        initial_dir = last_dir if last_dir and os.path.isdir(last_dir) else os.path.expanduser("~")
        p = filedialog.askdirectory(initialdir=initial_dir)
        if p:
            self.txt_dir.set(p)
            self.set_status("已选择目录")
            self.load_file_list(p)
            self.root.after(50, self.adjust_column_widths)

    def read_text_file(self, path):
        if not os.path.exists(path):
            return None
        for enc in ("utf-8", "utf-8-sig", "utf-16", "gb18030"):
            try:
                with open(path, "r", encoding=enc) as f:
                    return f.read()
            except UnicodeDecodeError:
                continue
            except Exception:
                break
        try:
            with open(path, "r", encoding="utf-8", errors="ignore") as f:
                return f.read()
        except Exception:
            return None

    def load_file_list(self, directory):
        # 清空
        for iid in self.files_tree.get_children():
            self.files_tree.delete(iid)
        for cb in self.tree_checks.values():
            try: cb.destroy()
            except Exception: pass
        for pb in self.tree_progress.values():
            try: pb.destroy()
            except Exception: pass
        self.selection_states.clear()
        self.selection_vars.clear()
        self.tree_checks.clear()
        self.progress_vars.clear()
        self.tree_progress.clear()
        self.error_detail.clear()
        self.file_chars.clear()
        self.spinner_active.clear()
        if self.spinner_job:
            try: self.root.after_cancel(self.spinner_job)
            except Exception: pass
            self.spinner_job = None

        if not os.path.isdir(directory):
            self.files_info_var.set("当前目录无效")
            return

        files = [f for f in sorted(os.listdir(directory)) if f.lower().endswith(".txt")]
        if not files:
            self.files_info_var.set("该目录下没有 TXT 文件")
            return

        for idx, fname in enumerate(files):
            full_path = os.path.join(directory, fname)
            # 大小
            try:
                size_kb = os.path.getsize(full_path) / 1024.0
                size_str = f"{size_kb:.1f}"
            except Exception:
                size_str = "-"

            # 字数（去空白）
            text = self.read_text_file(full_path)
            if text is None:
                chars = 0
            else:
                chars = len(text.replace(" ", "").replace("\n", "").replace("\t", ""))

            self.file_chars[fname] = chars
            est_str = self.estimate_duration_str(chars)

            iid = fname
            self.selection_states[iid] = True
            self.progress_vars[iid] = tk.DoubleVar(value=0.0)
            tag = "oddrow" if idx % 2 else "evenrow"
            self.files_tree.insert(
                "", "end", iid=iid,
                values=("", fname, size_str, str(chars), est_str, "待处理", ""),
                tags=(tag,)
            )

        self.update_selection_info()
        self.refresh_tree_overlays()
        self.root.after(50, self.adjust_column_widths)

    def refresh_tree_overlays(self):
        used_checks = set()
        used_progs = set()
        tree_h = self.files_tree.winfo_height()

        for iid in self.files_tree.get_children(""):
            # 选择列 -> Checkbutton（左右8px）
            bbox_sel = self.files_tree.bbox(iid, column="#1")
            if bbox_sel:
                x, y, w, h = bbox_sel
                if not (y + h < 0 or y > tree_h):
                    used_checks.add(iid)
                    var = self.selection_vars.get(iid)
                    if var is None:
                        var = tk.BooleanVar(value=self.selection_states.get(iid, True))
                        self.selection_vars[iid] = var
                    cb = self.tree_checks.get(iid)
                    if cb is None:
                        cb = ttk.Checkbutton(self.files_tree, variable=var,
                                             command=lambda i=iid: self.on_checkbutton_toggle(i),
                                             takefocus=False, cursor="hand2")
                        self.tree_checks[iid] = cb
                    cur = self.selection_states.get(iid, True)
                    if var.get() != cur: var.set(cur)
                    cb.place(x=x+8, y=y+2, width=max(0, w-16), height=h-4)
                else:
                    cb = self.tree_checks.get(iid)
                    if cb: cb.place_forget()

            # 进度列 -> Progressbar（注意列索引变化：progress 为第7列）
            bbox_prog = self.files_tree.bbox(iid, column="#7")
            if bbox_prog:
                x, y, w, h = bbox_prog
                if not (y + h < 0 or y > tree_h):
                    used_progs.add(iid)
                    var = self.progress_vars.get(iid)
                    if var is None:
                        var = tk.DoubleVar(value=0.0)
                        self.progress_vars[iid] = var
                    pb = self.tree_progress.get(iid)
                    if pb is None:
                        pb = ttk.Progressbar(self.files_tree, orient="horizontal", mode="determinate", maximum=100.0, variable=var)
                        self.tree_progress[iid] = pb
                    pb.place(x=x+6, y=y+6, width=max(40, w-12), height=h-12)
                else:
                    pb = self.tree_progress.get(iid)
                    if pb: pb.place_forget()

        for iid, cb in list(self.tree_checks.items()):
            if iid not in used_checks:
                cb.place_forget()
        for iid, pb in list(self.tree_progress.items()):
            if iid not in used_progs:
                pb.place_forget()

    def on_checkbutton_toggle(self, iid):
        self.selection_states[iid] = bool(self.selection_vars[iid].get())
        self.update_selection_info()

    def update_selection_info(self):
        total = len(self.selection_states)
        selected = sum(1 for v in self.selection_states.values() if v)
        self.files_info_var.set(f"已选择 {selected}/{total} 个文件")

    def select_all_files(self):
        for iid in self.files_tree.get_children():
            self.selection_states[iid] = True
            if iid in self.selection_vars:
                self.selection_vars[iid].set(True)
        self.update_selection_info()
        self.refresh_tree_overlays()

    def unselect_all_files(self):
        for iid in self.files_tree.get_children():
            self.selection_states[iid] = False
            if iid in self.selection_vars:
                self.selection_vars[iid].set(False)
        self.update_selection_info()
        self.refresh_tree_overlays()

    def invert_selection(self):
        for iid in self.files_tree.get_children():
            cur = self.selection_states.get(iid, True)
            self.selection_states[iid] = not cur
            if iid in self.selection_vars:
                self.selection_vars[iid].set(not cur)
        self.update_selection_info()
        self.refresh_tree_overlays()

    def on_tree_double_click(self, event):
        region = self.files_tree.identify_region(event.x, event.y)
        if region != "cell":
            return
        col = self.files_tree.identify_column(event.x)
        row = self.files_tree.identify_row(event.y)
        if not row:
            return
        if col == "#2":  # 名称列 -> 预览
            directory = self.txt_dir.get()
            path = os.path.join(directory, row)
            self.open_text_preview(path)
        elif col == "#6":  # 状态列（列顺序变更后为 #6）-> 查看错误详情
            self.show_error_detail(row)

    def open_text_preview(self, path):
        try:
            sysname = platform.system()
            if sysname == "Darwin":
                subprocess.run(["open", path], check=False)
            elif sysname == "Windows":
                os.startfile(path)  # type: ignore
            else:
                subprocess.run(["xdg-open", path], check=False)
            self.set_status(f"已打开预览: {os.path.basename(path)}")
        except Exception as e:
            self.set_status(f"预览失败: {e}")

    # 旋转指示器（主线程维护）
    def set_file_status(self, iid, status_text, spinning=False):
        def _apply():
            if spinning:
                self.spinner_active[iid] = {"base": status_text, "idx": 0}
                if self.spinner_job is None:
                    self.spinner_job = self.root.after(120, self._spinner_tick)
            else:
                if iid in self.spinner_active:
                    del self.spinner_active[iid]
                self.files_tree.set(iid, "status", status_text)
        self.root.after(0, _apply)

    def _spinner_tick(self):
        to_remove = []
        for iid, info in list(self.spinner_active.items()):
            base = info.get("base", "")
            idx = info.get("idx", 0)
            frame = self.spinner_frames[idx % len(self.spinner_frames)]
            try:
                self.files_tree.set(iid, "status", f"{base} {frame}")
            except Exception:
                to_remove.append(iid)
                continue
            info["idx"] = (idx + 1) % len(self.spinner_frames)
        for iid in to_remove:
            self.spinner_active.pop(iid, None)
        if self.spinner_active:
            self.spinner_job = self.root.after(120, self._spinner_tick)
        else:
            self.spinner_job = None

    def show_error_detail(self, iid):
        msg = self.error_detail.get(iid, "")
        if not msg:
            messagebox.showinfo("详情", "无错误详情。")
        else:
            messagebox.showerror("失败原因", msg)

    # ============ 试听 ============

    def preview_audio(self):
        def worker():
            voice_name = self.voice_var.get()
            text = f"你好，我是你的有声书助手，现在是{voice_name}为您朗读。"
            tmp_file = None
            try:
                with tempfile.NamedTemporaryFile(delete=False, suffix=".mp3") as tmp:
                    tmp_file = tmp.name
                self.edge.text_to_speech(
                    text=text, voice=self.voice_var.get(),
                    speed=self.speed_var.get(), pitch=self.pitch_var.get(),
                    volume=self.volume_var.get(), output_file=tmp_file
                )
                self.set_status("试听文件生成成功，开始播放...")
                if platform.system() == "Darwin":
                    subprocess.run(["afplay", tmp_file], check=True, capture_output=True)
                elif platform.system() == "Windows":
                    proc = subprocess.Popen(["start", "/wait", tmp_file], shell=True,
                                            stdout=subprocess.PIPE, stderr=subprocess.PIPE)
                    proc.wait()
                else:
                    subprocess.run(["xdg-open", tmp_file], check=True)
                self.set_status("试听播放完成。")
            except FileNotFoundError:
                self.set_status("播放失败: 系统命令未找到（macOS 需 afplay）")
            except subprocess.CalledProcessError as e:
                self.set_status(f"播放命令失败: {e}")
                err_msg = str(e)
                self.root.after(0, lambda msg=err_msg: messagebox.showerror("播放错误", f"播放器无法启动，请检查是否安装了ffmpeg。\n错误详情：{msg}"))
            except Exception as e:
                self.set_status(f"试听失败: {e}")
                err_msg = str(e)
                self.root.after(0, lambda msg=err_msg: messagebox.showerror("试听失败", f"无法连接到语音服务。\n\n可能原因：\n1.未运行 Install Certificates.command\n2.网络无法连接微软服务器\n\n技术错误信息：\n{msg}"))
            finally:
                if tmp_file and os.path.exists(tmp_file):
                    try: os.remove(tmp_file)
                    except Exception: pass
        threading.Thread(target=worker, daemon=True).start()

    # ============ 生成主流程 ============

    def start_generation(self):
        self.stop_flag = False
        if not self._has_ffmpeg():
            self.set_status("未检测到 ffmpeg，若分段>1将无法合并；将尽量退化处理。")
        self.set_status("开始转换任务...")
        threading.Thread(target=self.generate, daemon=True).start()

    def stop_generation(self):
        self.stop_flag = True
        self.set_status("用户请求停止，正在中止任务...")

    def open_output_dir(self):
        txt_dir = self.txt_dir.get()
        if not txt_dir or not os.path.isdir(txt_dir):
            messagebox.showerror("错误", "请先选择有效的TXT目录")
            return
        out_dir = os.path.join(txt_dir, "Audio")
        os.makedirs(out_dir, exist_ok=True
        )
        if platform.system() == "Darwin": os.system(f'open "{out_dir}"')
        elif platform.system() == "Windows": os.startfile(out_dir)  # type: ignore
        else: os.system(f'xdg-open "{out_dir}"')

    def on_closing(self):
        self.stop_flag = True
        edge_voice_name = VOICE_MAPPING.get(self.voice_var.get(), "zh-CN-XiaoxiaoNeural")
        self.config["edge"]["voice_name"] = edge_voice_name
        self.config["edge"]["speed"] = self.speed_var.get()
        self.config["edge"]["pitch"] = self.pitch_var.get()
        self.config["edge"]["volume"] = self.volume_var.get()
        self.config["last_txt_dir"] = self.txt_dir.get()
        self.config["merge_audio"] = self.merge_var.get()
        self.config["target_duration"] = self.target_duration_var.get()
        self.config["words_per_minute"] = self.wpm_var.get()
        save_config(self.config)
        self.root.destroy()

    def estimate_duration(self, text):
        wpm = max(1, self.wpm_var.get())
        word_count = len(text)
        return word_count / wpm

    def get_selected_files(self):
        directory = self.txt_dir.get()
        if not directory or not os.path.isdir(directory):
            return []
        return [iid for iid in self.files_tree.get_children() if self.selection_states.get(iid, False)]

    def set_file_progress(self, iid, percent):
        var = self.progress_vars.get(iid)
        if var is None:
            var = tk.DoubleVar(value=0.0)
            self.progress_vars[iid] = var
        self.root.after(0, lambda v=var, p=percent: v.set(max(0.0, min(100.0, float(p)))))

    def set_error(self, iid, exc):
        self.error_detail[iid] = str(exc)

    # TTS 重试
    def tts_with_retry(self, text, output_file, iid_for_error=None, max_retries=3):
        last_exc = None
        parent = os.path.dirname(output_file) or "."
        os.makedirs(parent, exist_ok=True)

        for n in range(1, max_retries + 1):
            try:
                # 清理同名残留文件
                if os.path.exists(output_file):
                    try:
                        os.remove(output_file)
                    except Exception:
                        pass

                self.edge.text_to_speech(
                    text=text, voice=self.voice_var.get(),
                    speed=self.speed_var.get(), pitch=self.pitch_var.get(),
                    volume=self.volume_var.get(), output_file=output_file
                )

                # 确认文件已生成且非空
                if os.path.exists(output_file) and os.path.getsize(output_file) > 0:
                    return True
                else:
                    raise RuntimeError(f"TTS输出为空或未生成: {os.path.basename(output_file)}")

            except Exception as e:
                last_exc = e
                # 逐步退避
                if n < max_retries:
                    time.sleep(min(1.5, 0.5 * n))

        if iid_for_error:
            self.set_error(iid_for_error, last_exc)
        return False
        last_exc = None
        for n in range(1, max_retries + 1):
            try:
                self.edge.text_to_speech(
                    text=text, voice=self.voice_var.get(),
                    speed=self.speed_var.get(), pitch=self.pitch_var.get(),
                    volume=self.volume_var.get(), output_file=output_file
                )
                return True
            except Exception as e:
                last_exc = e
                time.sleep(min(1.5, 0.5 * n))
        if iid_for_error:
            self.set_error(iid_for_error, last_exc)
        return False

    def generate(self):
        txt_dir = self.txt_dir.get()
        if not txt_dir or not os.path.isdir(txt_dir):
            self.root.after(0, lambda: messagebox.showerror("错误", "请选择有效的TXT目录"))
            return
        out_dir = os.path.join(txt_dir, "Audio")
        os.makedirs(out_dir, exist_ok=True)

        files = self.get_selected_files()
        if not files:
            self.set_status("请先在文件列表中勾选至少一个TXT文件。")
            messagebox.showwarning("提示", "请在文件列表中勾选至少一个TXT文件。")
            return

        if not self.merge_var.get():
            self.generate_single_files(files, txt_dir, out_dir)
        else:
            self.generate_merged_files(files, txt_dir, out_dir)

        if self.stop_flag:
            for f in files:
                cur = self.files_tree.set(f, "status")
                if cur not in ("✅ 已完成", "✅ 已完成，", "失败", "已存在(跳过)"):
                    self.set_file_status(f, "已中断", spinning=False)
            self.set_status("任务已中断。")
        else:
            self.set_status("所有任务处理完成。")

    # ----- 单文件输出 -----
    def generate_single_files(self, files, txt_dir, out_dir):
        for i, f in enumerate(files, 1):
            if self.stop_flag: break
            ipath = os.path.join(txt_dir, f)
            safe_base = sanitize_filename(os.path.splitext(f)[0])
            opath = os.path.join(out_dir, safe_base + ".mp3")

            if os.path.exists(opath):
                self.set_file_progress(f, 100.0)
                # 读取真实时长
                dur = self.get_mp3_duration_str(opath)
                if dur:
                    self.set_file_status(f, f"已存在(跳过)（🕒{dur}）", spinning=False)
                else:
                    self.set_file_status(f, "已存在(跳过)", spinning=False)
                continue

            text = self.read_text_file(ipath)
            if text is None:
                self.set_file_status(f, "失败：无法读取文件", spinning=False)
                self.set_error(f, f"无法读取文件: {ipath}")
                continue
            text = text.strip()
            paras = preprocess_text(text)
            total_paras = max(1, len(paras))
            self.set_file_status(f, f"已拆分为 {total_paras} 段", spinning=False)

            tempfiles = []
            ok = True

            for j, p in enumerate(paras, 1):
                if self.stop_flag:
                    ok = False
                    self.set_file_status(f, "已中断", spinning=False)
                    break

                tfile = os.path.join(out_dir, f"temp_%d_%d.mp3" % (i, j))
                bn = os.path.basename(tfile)
                self.set_file_status(f, f"合成中 {bn} ({j}/{total_paras})", spinning=True)

                if not self.tts_with_retry(p, tfile, iid_for_error=f, max_retries=3):
                    ok = False
                    self.set_file_status(f, f"合成失败 {bn}", spinning=False)
                    break

                tempfiles.append(tfile)
                self.set_file_progress(f, j / total_paras * 100.0)

            if ok and not self.stop_flag:
                # 合并与导出
                self.set_file_status(f, f"合并音频（{len(tempfiles)} 段）", spinning=True)
                if len(tempfiles) == 1:
                    try:
                        shutil.move(tempfiles[0], opath)
                        self.set_file_progress(f, 100.0)
                        dur = self.get_mp3_duration_str(opath)
                        if dur:
                            self.set_file_status(f, f"✅ 已完成，🕒{dur}", spinning=False)
                        else:
                            self.set_file_status(f, "✅ 已完成", spinning=False)
                    except Exception as e:
                        self.set_error(f, e)
                        self.set_file_status(f, "失败：导出错误", spinning=False)
                else:
                    try:
                        combined = AudioSegment.empty()
                        for tf in tempfiles:
                            audio = AudioSegment.from_file(tf, format="mp3")
                            combined += audio
                            combined += AudioSegment.silent(duration=1000)
                        self.set_file_status(f, "导出 MP3...", spinning=True)
                        combined.export(opath, format="mp3")
                        for tf in tempfiles:
                            try: os.remove(tf)
                            except Exception: pass
                        self.set_file_progress(f, 100.0)
                        # 直接用 combined 的时长，避免再次解码
                        dur = self.seconds_to_str(int(round(len(combined) / 1000)))
                        self.set_file_status(f, f"✅ 已完成，🕒{dur}", spinning=False)
                    except Exception as e:
                        try:
                            if len(tempfiles) == 1 and os.path.exists(tempfiles[0]):
                                shutil.move(tempfiles[0], opath)
                                self.set_file_progress(f, 100.0)
                                dur = self.get_mp3_duration_str(opath)
                                if dur:
                                    self.set_file_status(f, f"✅ 已完成(简化导出)，🕒{dur}", spinning=False)
                                else:
                                    self.set_file_status(f, "✅ 已完成(简化导出)", spinning=False)
                            else:
                                self.set_error(f, e)
                                self.set_file_status(f, "失败：合并/导出错误", spinning=False)
                        finally:
                            for tf in tempfiles:
                                if os.path.exists(tf):
                                    try: os.remove(tf)
                                    except Exception: pass
            else:
                for tf in tempfiles:
                    try: os.remove(tf)
                    except Exception: pass

    # ----- 合并输出 -----
    def generate_merged_files(self, files, txt_dir, out_dir):
        target_duration = self.target_duration_var.get()
        part_num = 1
        current_text = ""
        current_duration = 0.0
        current_files = []

        for i, f in enumerate(files, 1):
            if self.stop_flag: break

            ipath = os.path.join(txt_dir, f)
            text = self.read_text_file(ipath)
            if text is None:
                self.set_file_status(f, "失败：无法读取文件", spinning=False)
                self.set_error(f, f"无法读取文件: {ipath}")
                continue
            text = text.strip()

            file_duration = self.estimate_duration(text)

            if file_duration >= target_duration:
                if current_text and not self.stop_flag:
                    self.process_text_chunk(current_text, out_dir, part_num, current_files)
                    part_num += 1
                    current_text = ""
                    current_duration = 0.0
                    current_files = []

                opath = self.generate_output_filename(out_dir, part_num, [f])
                self.process_single_long_file(text, opath, f)
                part_num += 1
                continue

            self.set_file_status(f, "等待合并", spinning=False)
            if current_duration + file_duration <= target_duration:
                current_text += ("\n\n" + text) if current_text else text
                current_duration += file_duration
                current_files.append(f)
            else:
                if current_text and not self.stop_flag:
                    self.process_text_chunk(current_text, out_dir, part_num, current_files)
                    part_num += 1
                current_text = text
                current_duration = file_duration
                current_files = [f]

        if current_text and not self.stop_flag:
            self.process_text_chunk(current_text, out_dir, part_num, current_files)

    def generate_output_filename(self, out_dir, part_num, file_list):
        if len(file_list) == 1:
            base_name = sanitize_filename(os.path.splitext(file_list[0])[0])
            return os.path.join(out_dir, f"{base_name}.mp3")
        else:
            first_file = sanitize_filename(os.path.splitext(file_list[0])[0])
            last_file = sanitize_filename(os.path.splitext(file_list[-1])[0])
            return os.path.join(out_dir, f"{first_file}_到_{last_file}.mp3")

    def process_text_chunk(self, text, out_dir, part_num, file_list):
        if self.stop_flag or not file_list:
            return
        opath = self.generate_output_filename(out_dir, part_num, file_list)

        if os.path.exists(opath):
            dur = self.get_mp3_duration_str(opath)
            for name in file_list:
                self.set_file_progress(name, 100.0)
                if dur:
                    self.set_file_status(name, f"已存在(跳过)（🕒{dur}）", spinning=False)
                else:
                    self.set_file_status(name, "已存在(跳过)", spinning=False)
            return

        # 只让第一个文件作为“leader”显示旋转，其它显示合并中
        leader = file_list[0]
        paras = preprocess_text(text)
        total_paras = max(1, len(paras))

        for idx, name in enumerate(file_list):
            if idx == 0:
                self.set_file_status(name, f"合并段落 {part_num}：已拆分为 {total_paras} 段", spinning=True)
            else:
                self.set_file_status(name, f"等待合并（{part_num}，{total_paras} 段）", spinning=False)
            self.set_file_progress(name, 0.0)

        tempfiles = []
        ok = True

        for j, p in enumerate(paras, 1):
            if self.stop_flag:
                ok = False
                for name in file_list:
                    self.set_file_status(name, "已中断", spinning=False)
                break

            tfile = os.path.join(out_dir, f"temp_part{part_num}_{j}.mp3")
            bn = os.path.basename(tfile)

            # 仅 leader 行显示“合成中 …”+ 旋转，其他行显示“合并中（j/n）”
            self.set_file_status(leader, f"合成中 {bn} ({j}/{total_paras})", spinning=True)
            for name in file_list[1:]:
                self.set_file_status(name, f"合并中（{j}/{total_paras}）", spinning=False)

            success = self.tts_with_retry(p, tfile, iid_for_error=file_list[0], max_retries=3)
            if not success:
                ok = False
                self.set_file_status(leader, f"合成失败 {bn}", spinning=False)
                for name in file_list[1:]:
                    self.set_file_status(name, "失败：合并中断", spinning=False)
                break

            tempfiles.append(tfile)
            percent = j / total_paras * 100.0
            for name in file_list:
                self.set_file_progress(name, percent)

        if ok and not self.stop_flag:
            for name in file_list:
                self.set_file_status(name, f"合并音频（{len(tempfiles)} 段）", spinning=(name == leader))
            try:
                if len(tempfiles) == 1:
                    shutil.move(tempfiles[0], opath)
                    dur = self.get_mp3_duration_str(opath)
                else:
                    combined = AudioSegment.empty()
                    for tf in tempfiles:
                        audio = AudioSegment.from_file(tf, format="mp3")
                        combined += audio
                        combined += AudioSegment.silent(duration=1000)
                    for name in file_list:
                        self.set_file_status(name, "导出 MP3...", spinning=(name == leader))
                    combined.export(opath, format="mp3")
                    dur = self.seconds_to_str(int(round(len(combined) / 1000)))

                for tf in tempfiles:
                    if os.path.exists(tf):
                        try: os.remove(tf)
                        except Exception: pass

                for name in file_list:
                    self.set_file_progress(name, 100.0)
                    if dur:
                        self.set_file_status(name, f"✅ 已完成，🕒{dur}", spinning=False)
                    else:
                        self.set_file_status(name, "✅ 已完成", spinning=False)

            except Exception as e:
                for name in file_list:
                    self.set_error(name, e)
                    self.set_file_status(name, "失败：合并/导出错误", spinning=False)
                for tf in tempfiles:
                    if os.path.exists(tf):
                        try: os.remove(tf)
                        except Exception: pass
        if self.stop_flag or not file_list:
            return
        opath = self.generate_output_filename(out_dir, part_num, file_list)

        if os.path.exists(opath):
            dur = self.get_mp3_duration_str(opath)
            for name in file_list:
                self.set_file_progress(name, 100.0)
                if dur:
                    self.set_file_status(name, f"已存在(跳过)（🕒{dur}）", spinning=False)
                else:
                    self.set_file_status(name, "已存在(跳过)", spinning=False)
            return

        paras = preprocess_text(text)
        total_paras = max(1, len(paras))
        for name in file_list:
            self.set_file_status(name, f"合并段落 {part_num}：已拆分为 {total_paras} 段", spinning=True)
            self.set_file_progress(name, 0.0)

        tempfiles = []
        ok = True

        for j, p in enumerate(paras, 1):
            if self.stop_flag:
                ok = False
                for name in file_list:
                    self.set_file_status(name, "已中断", spinning=False)
                break

            tfile = os.path.join(out_dir, f"temp_part{part_num}_{j}.mp3")
            bn = os.path.basename(tfile)
            for name in file_list:
                self.set_file_status(name, f"合成中 {bn} ({j}/{total_paras})", spinning=True)

            success = self.tts_with_retry(p, tfile, iid_for_error=file_list[0], max_retries=3)
            if not success:
                ok = False
                for name in file_list:
                    self.set_file_status(name, f"合成失败 {bn}", spinning=False)
                break

            tempfiles.append(tfile)
            percent = j / total_paras * 100.0
            for name in file_list:
                self.set_file_progress(name, percent)

        if ok and not self.stop_flag:
            for name in file_list:
                self.set_file_status(name, f"合并音频（{len(tempfiles)} 段）", spinning=True)
            try:
                if len(tempfiles) == 1:
                    shutil.move(tempfiles[0], opath)
                    dur = self.get_mp3_duration_str(opath)
                else:
                    combined = AudioSegment.empty()
                    for tf in tempfiles:
                        audio = AudioSegment.from_file(tf, format="mp3")
                        combined += audio
                        combined += AudioSegment.silent(duration=1000)
                    for name in file_list:
                        self.set_file_status(name, "导出 MP3...", spinning=True)
                    combined.export(opath, format="mp3")
                    dur = self.seconds_to_str(int(round(len(combined) / 1000)))

                for tf in tempfiles:
                    if os.path.exists(tf):
                        try: os.remove(tf)
                        except Exception: pass
                for name in file_list:
                    self.set_file_progress(name, 100.0)
                    if dur:
                        self.set_file_status(name, f"✅ 已完成，🕒{dur}", spinning=False)
                    else:
                        self.set_file_status(name, "✅ 已完成", spinning=False)
            except Exception as e:
                for name in file_list:
                    self.set_error(name, e)
                    self.set_file_status(name, "失败：合并/导出错误", spinning=False)
                for tf in tempfiles:
                    if os.path.exists(tf):
                        try: os.remove(tf)
                        except Exception: pass

    def process_single_long_file(self, text, output_path, filename_iid):
        if self.stop_flag:
            self.set_file_status(filename_iid, "已中断", spinning=False)
            return
        if os.path.exists(output_path):
            self.set_file_progress(filename_iid, 100.0)
            dur = self.get_mp3_duration_str(output_path)
            if dur:
                self.set_file_status(filename_iid, f"已存在(跳过)（🕒{dur}）", spinning=False)
            else:
                self.set_file_status(filename_iid, "已存在(跳过)", spinning=False)
            return

        paras = preprocess_text(text)
        total_paras = max(1, len(paras))
        self.set_file_status(filename_iid, f"已拆分为 {total_paras} 段", spinning=False)

        tempfiles = []
        ok = True

        for j, p in enumerate(paras, 1):
            if self.stop_flag:
                ok = False
                self.set_file_status(filename_iid, "已中断", spinning=False)
                break

            tfile = os.path.join(os.path.dirname(output_path), f"temp_long_{j}.mp3")
            bn = os.path.basename(tfile)
            self.set_file_status(filename_iid, f"合成中 {bn} ({j}/{total_paras})", spinning=True)

            if not self.tts_with_retry(p, tfile, iid_for_error=filename_iid, max_retries=3):
                ok = False
                self.set_file_status(filename_iid, f"合成失败 {bn}", spinning=False)
                break

            tempfiles.append(tfile)
            self.set_file_progress(filename_iid, j / total_paras * 100.0)

        if ok and not self.stop_flag:
            self.set_file_status(filename_iid, f"合并音频（{len(tempfiles)} 段）", spinning=True)
            try:
                if len(tempfiles) == 1:
                    shutil.move(tempfiles[0], output_path)
                    dur = self.get_mp3_duration_str(output_path)
                else:
                    combined = AudioSegment.empty()
                    for tf in tempfiles:
                        audio = AudioSegment.from_file(tf, format="mp3")
                        combined += audio
                        combined += AudioSegment.silent(duration=1000)
                    self.set_file_status(filename_iid, "导出 MP3...", spinning=True)
                    combined.export(output_path, format="mp3")
                    dur = self.seconds_to_str(int(round(len(combined) / 1000)))

                for tf in tempfiles:
                    if os.path.exists(tf):
                        try: os.remove(tf)
                        except Exception: pass
                self.set_file_progress(filename_iid, 100.0)
                if dur:
                    self.set_file_status(filename_iid, f"✅ 已完成，🕒{dur}", spinning=False)
                else:
                    self.set_file_status(filename_iid, "✅ 已完成", spinning=False)
            except Exception as e:
                self.set_error(filename_iid, e)
                self.set_file_status(filename_iid, "失败：合并/导出错误", spinning=False)
                for tf in tempfiles:
                    if os.path.exists(tf):
                        try: os.remove(tf)
                        except Exception: pass

    def import_epub(self):
        """选择一个 .epub 文件，调用 convert_epub_to_txt 转成章节 TXT，并自动切换目录与刷新列表。"""
        path = filedialog.askopenfilename(
            title="选择 EPUB",
            filetypes=[("EPUB 文件", "*.epub"), ("所有文件", "*.*")]
        )
        if not path:
            return
        if not path.lower().endswith(".epub"):
            messagebox.showerror("格式错误", "请选择 .epub 文件")
            return
        try:
            self.set_status("正在从EPUB提取章节文本…")
            out_dir, converted_count, total_files = convert_epub_to_txt(
                path,
                progress_callback=lambda s: self.set_status(f"EPUB：{s}")
            )
            # 切换到输出目录并刷新列表
            self.txt_dir.set(out_dir)
            self.load_file_list(out_dir)
            self.set_status(f"EPUB转换完成：生成 {converted_count} 章 / {total_files} 个TXT")
        except Exception as e:
            messagebox.showerror("EPUB转换失败", str(e))
            self.set_status("EPUB转换失败")
  
    def run(self):
        """启动 Tkinter 主循环"""
        self.root.mainloop()

if __name__ == "__main__":
    app = AudiobookGenerator()
    # 如果类里有 run 方法就调用，否则直接进入主循环
    if hasattr(app, "run"):
        app.run()
    else:
        app.root.mainloop()