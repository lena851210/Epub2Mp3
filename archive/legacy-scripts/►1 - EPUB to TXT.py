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

# ====== GUI ======
class EPUBConverterApp:
    def __init__(self, root):
        self.root = root
        self.root.title("EPUB → TXT 转换器")
        self.root.geometry("500x180")
        self.root.resizable(False, False)

        # 样式设置
        self.style = ttk.Style(root)
        try:
            self.style.theme_use("clam")
        except Exception:
            pass

        self.main_frame = ttk.Frame(root, padding=(18,16))
        self.main_frame.pack(fill="both", expand=True)

        # 标题
        self.lbl = ttk.Label(self.main_frame, text="EPUB 转 TXT 转换器", font=("Arial", 14, "bold"))
        self.lbl.grid(row=0, column=0, columnspan=2, sticky="w", pady=(4,12))

        # 选择文件按钮
        self.btn_convert = ttk.Button(self.main_frame, text="选择 EPUB 并转换", command=self.start_conversion)
        self.btn_convert.grid(row=1, column=0, padx=(0,12), pady=6, sticky="ew")

        # 打开输出文件夹按钮
        self.btn_open = ttk.Button(self.main_frame, text="打开输出文件夹", command=self.open_last_output_folder)
        self.btn_open.grid(row=1, column=1, pady=6, sticky="ew")

        # 状态显示
        self.status_var = tk.StringVar()
        self.status_var.set("准备就绪")
        self.status_label = ttk.Label(self.main_frame, textvariable=self.status_var, font=("Arial", 10))
        self.status_label.grid(row=2, column=0, columnspan=2, sticky="w", pady=(12,6))

        # 列权重设置
        self.main_frame.columnconfigure(0, weight=1)
        self.main_frame.columnconfigure(1, weight=1)

    def start_conversion(self):
        epub_path = filedialog.askopenfilename(
            title="选择 EPUB 文件",
            filetypes=[("EPUB 文件", "*.epub")]
        )
        if not epub_path:
            return
            
        # 禁用按钮，开始转换
        self.btn_convert.config(state='disabled')
        self.btn_open.config(state='disabled')
        self.status_var.set("正在转换，请稍候...")
        
        # 在新线程中执行转换
        thread = threading.Thread(target=self.convert_epub, args=(epub_path,))
        thread.daemon = True
        thread.start()

    def convert_epub(self, epub_path):
        try:
            def update_progress(message):
                self.root.after(0, lambda: self.status_var.set(message))
                
            out_dir, converted_count, total_files = convert_epub_to_txt(epub_path, update_progress)
            
            self.root.after(0, lambda: self.on_conversion_complete(out_dir, converted_count, total_files))
        except Exception as e:
            self.root.after(0, lambda: self.on_conversion_error(str(e)))

    def on_conversion_complete(self, out_dir, converted_count, total_files):
        self.btn_convert.config(state='normal')
        self.btn_open.config(state='normal')
        
        # 显示转换结果
        self.status_var.set(f"转换完成！共生成 {total_files} 个文件")
        
        # 自动打开输出目录
        try:
            subprocess.run(["open", out_dir])
        except:
            pass

    def on_conversion_error(self, error_msg):
        self.btn_convert.config(state='normal')
        self.btn_open.config(state='normal')
        self.status_var.set("转换失败")
        messagebox.showerror("错误", f"转换失败：{error_msg}")

    def open_last_output_folder(self):
        global last_output_dir
        if last_output_dir and os.path.isdir(last_output_dir):
            try:
                subprocess.run(["open", last_output_dir])
            except:
                messagebox.showerror("错误", "无法打开输出目录")
        else:
            messagebox.showinfo("提示", "还没有可打开的最近输出目录（请先执行一次转换）。")

def main():
    root = tk.Tk()
    app = EPUBConverterApp(root)
    root.mainloop()

if __name__ == "__main__":
    main()