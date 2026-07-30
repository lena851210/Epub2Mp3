import os
import re
import json
import tkinter as tk
from tkinter import filedialog, messagebox
from ebooklib import epub
from bs4 import BeautifulSoup
from difflib import SequenceMatcher

CONFIG_FILE = "config.json"

# ---------------- 清洗规则配置 ----------------
MATH_REPLACEMENTS = {
    r'\bmc2\b': 'm·c²',
    r'([a-zA-Z])2\b': r'\1²',
    r'([a-zA-Z])3\b': r'\1³',
}

SYMBOL_REPLACEMENTS = {
    "×": "乘", "÷": "除以", "±": "正负",
    "≈": "约等于", "≠": "不等于", "≤": "小于等于", "≥": "大于等于",
    "∨": "◆", "◊": "◆·", "※": "◆"
}

# ---------------- 工具函数 ----------------
def save_last_path(path):
    config = {"last_dir": path}
    with open(CONFIG_FILE, "w", encoding="utf-8") as f:
        json.dump(config, f)

def load_last_path():
    if os.path.exists(CONFIG_FILE):
        try:
            return json.load(open(CONFIG_FILE, "r", encoding="utf-8")).get("last_dir", None)
        except:
            return None
    return None

def sanitize_filename(name):
    return re.sub(r'[\\/*?:"<>|]', "_", str(name)).strip()

# ---------------- 文本清洗 ----------------
def prepare_for_tts(text: str) -> str:
    """优化文本，适合TTS朗读"""
    if not text:
        return ""

    # 去除脚注 [数字]
    text = re.sub(r'^\[\d+\]\s*$', '', text, flags=re.MULTILINE)
    text = re.sub(r'\[\d+\]', '', text)

    # 修复字母+数字断行
    text = re.sub(r'([a-zA-Z])\s*\n\s*([0-9])', r'\1\2', text)

    # 数学公式替换
    for pattern, repl in MATH_REPLACEMENTS.items():
        text = re.sub(pattern, repl, text, flags=re.IGNORECASE)

    # 特殊符号替换
    for k, v in SYMBOL_REPLACEMENTS.items():
        text = text.replace(k, v)

    # 空格统一
    text = re.sub(r"\s+", " ", text)

    # 标点优化
    text = text.replace("---", "——").replace("--", "——")

    return text.strip()

def normalize_paragraphs(raw_text: str) -> str:
    """段落规整：去多余空行，保证格式自然"""
    lines = [line.strip() for line in raw_text.split("\n")]
    clean_lines = []
    
    for i, line in enumerate(lines):
        line = prepare_for_tts(line)
        if not line:
            continue
            
        # 处理开头是标点符号的行
        if line and line[0] in "，。！？、；：":
            # 如果上一行存在，将当前行合并到上一行
            if clean_lines:
                clean_lines[-1] += line
                continue
            else:
                # 如果是第一行，去掉开头的标点
                line = line.lstrip("，。！？、；：")
                if not line:  # 如果去掉标点后为空，跳过
                    continue
        
        clean_lines.append(line)
    
    # 确保段落之间有适当的空行
    result = "\n\n".join(clean_lines)
    
    # 处理连续的空行
    result = re.sub(r'\n\s*\n', '\n\n', result)
    
    return result

# ---------------- 相似度函数 ----------------
def is_similar_text(t1, t2, threshold=0.8):
    if not t1 or not t2:
        return False
    clean1 = re.sub(r'[^\w]', '', t1.lower())
    clean2 = re.sub(r'[^\w]', '', t2.lower())
    if clean1 == clean2:
        return True
    return SequenceMatcher(None, clean1, clean2).ratio() >= threshold

# ---------------- 提取元数据+toc ----------------
def extract_metadata_and_toc(epub_file):
    book = epub.read_epub(epub_file)

    metadata = {
        "title": (book.get_metadata('DC', 'title')[0][0] if book.get_metadata('DC', 'title') else "未知书名"),
        "author": ("、".join(c[0] for c in book.get_metadata('DC', 'creator'))
                   if book.get_metadata('DC', 'creator') else "未知作者"),
        "publisher": (book.get_metadata('DC', 'publisher')[0][0]
                      if book.get_metadata('DC', 'publisher') else ""),
        "language": (book.get_metadata('DC', 'language')[0][0]
                     if book.get_metadata('DC', 'language') else ""),
        "description": (book.get_metadata('DC', 'description')[0][0]
                        if book.get_metadata('DC', 'description') else "")
    }

    toc_entries = []

    def parse_toc(toc):
        for i in toc:
            if isinstance(i, epub.Link):
                toc_entries.append({"title": i.title, "href": i.href})
            elif isinstance(i, (list, tuple)):
                parse_toc(i)

    parse_toc(book.toc)
    return book, metadata, toc_entries

# ---------------- 提取章节 ----------------
def extract_chapter_text(book, chapter_href, chapter_title=None):
    href_clean = chapter_href.split("#")[0]
    item = book.get_item_with_href(href_clean)
    if not item:
        return ""

    try:
        soup = BeautifulSoup(item.get_content(), "html.parser")
    except:
        return ""

    # 删除冗余标签
    for tag in soup(["script", "style", "nav", "aside", "footer"]):
        tag.decompose()

    # 处理首字下沉 (dropcap) 情况
    for span in soup.find_all("span", class_=re.compile(r"dropcap")):
        # 将首字下沉的span转换为普通文本，避免换行问题
        span.replace_with(span.get_text())

    # 删除重复标题
    for tag in soup(["h1", "h2", "h3", "h4", "h5", "h6"]):
        ttext = tag.get_text(strip=True)
        if chapter_title and is_similar_text(ttext, chapter_title):
            tag.decompose()
        else:
            new_tag = soup.new_tag("p")
            new_tag.string = ttext
            tag.replace_with(new_tag)

    # 删除脚注
    for span in soup.find_all("span", class_="super"):
        span.decompose()
    for span in soup.find_all("span"):
        if re.fullmatch(r"\[\d+\]", span.get_text(strip=True)):
            span.decompose()

    # 提取文本
    text = soup.get_text(separator="\n", strip=True)
    text = re.sub(r"\[\d+\]", "", text)
    
    # 处理特殊情况：修复因标签导致的断行问题
    text = re.sub(r'([^。！？：；\n])\n([^「『《（【])', r'\1\2', text)
    
    return normalize_paragraphs(text)

# ---------------- 切分长章节 ----------------
def split_long_text(text, max_size=3000):
    # 按段落分割
    paras = [p.strip() for p in text.split("\n\n") if p.strip()]
    parts, current, length = [], "", 0
    
    for para in paras:
        para_len = len(para)
        
        # 如果当前段落加入后超过限制，且当前部分不为空，则结束当前部分
        if length + para_len > max_size and current:
            parts.append(current.strip())
            current, length = "", 0
        
        # 如果段落本身超过限制，则必须拆分
        if para_len > max_size:
            # 如果当前部分不为空，先保存当前部分
            if current:
                parts.append(current.strip())
                current, length = "", 0
            
            # 将超长段落拆分为多个部分
            start = 0
            while start < para_len:
                # 找到合适的拆分点（尽量在句末拆分）
                end = start + max_size
                if end < para_len:
                    # 尝试在句号、问号等标点处拆分
                    for split_point in range(min(end + 100, para_len) - 1, end - 100, -1):
                        if split_point <= start:
                            break
                        if para[split_point] in '。！？.!?；;':
                            end = split_point + 1
                            break
                
                chunk = para[start:end].strip()
                if chunk:  # 确保非空
                    parts.append(chunk)
                start = end
        else:
            current += para + "\n\n"
            length += para_len
    
    # 添加最后的部分
    if current:
        parts.append(current.strip())
    
    return parts

# ---------------- 保存章节 ----------------
def save_chapter(save_dir, title, content, is_first, metadata, part_num=None, index=1):
    safe_title = sanitize_filename(title if title else f"章节{index}")
    
    # 简化文件名：使用数字后缀而不是_partX
    if part_num:
        filename = f"{index:03d}_{safe_title}_{part_num}.txt"
    else:
        filename = f"{index:03d}_{safe_title}.txt"

    with open(os.path.join(save_dir, filename), "w", encoding="utf-8") as f:
        if is_first:
            f.write(f"《{metadata['title']}》\n作者：{metadata['author']}\n")
            if metadata["publisher"]:
                f.write(f"出版社：{metadata['publisher']}\n")
            if metadata["language"]:
                f.write(f"语言：{metadata['language']}\n")
            if metadata["description"]:
                f.write(f"简介：{metadata['description'][:200]}...\n")
            f.write("\n=== 有声朗读正文开始 ===\n\n")
        
        # 简化标题显示
        if part_num:
            f.write(f"【{title} ({part_num})】\n\n")
        else:
            f.write(f"【{title}】\n\n")
        
        f.write(content)
    
    return filename

# ---------------- 主流程 ----------------
def start_conversion():
    initial_dir = load_last_path()
    epub_file = filedialog.askopenfilename(
        title="选择 EPUB 文件",
        filetypes=[("EPUB files", "*.epub")],
        initialdir=initial_dir if initial_dir else os.path.expanduser("~")
    )
    if not epub_file:
        return
    
    save_last_path(os.path.dirname(epub_file))
    
    # 让用户选择父目录
    parent_dir = filedialog.askdirectory(
        title="选择保存父目录",
        initialdir=os.path.dirname(epub_file) if epub_file else os.path.expanduser("~")
    )
    if not parent_dir:
        return
    
    # 创建输出目录，使用EPUB文件名作为目录名
    epub_filename = os.path.splitext(os.path.basename(epub_file))[0]
    save_dir = os.path.join(parent_dir, epub_filename)
    
    # 如果目录已存在，添加数字后缀
    counter = 1
    original_save_dir = save_dir
    while os.path.exists(save_dir):
        save_dir = f"{original_save_dir}_{counter}"
        counter += 1
    
    os.makedirs(save_dir, exist_ok=True)

    try:
        book, metadata, toc = extract_metadata_and_toc(epub_file)
    except Exception as e:
        messagebox.showerror("错误", f"解析 EPUB 失败：{e}")
        return

    if not toc:
        messagebox.showerror("错误", "未找到目录 TOC。")
        return

    total, index = 0, 1
    for entry in toc:
        title = entry["title"] or f"章节{index}"
        raw_text = extract_chapter_text(book, entry["href"], title)
        if not raw_text:
            continue

        if len(raw_text) <= 3000:
            save_chapter(save_dir, title, raw_text, is_first=(index == 1), metadata=metadata, index=index)
            total += 1
        else:
            parts = split_long_text(raw_text, 3000)
            for part_num, part in enumerate(parts, 1):
                save_chapter(save_dir, title, part,
                             is_first=(index == 1 and part_num == 1),
                             metadata=metadata, part_num=part_num, index=index)
                total += 1
        index += 1

    messagebox.showinfo("完成", f"书籍《{metadata['title']}》转换完成，共生成 {total} 个文件！\n保存位置: {save_dir}")

# ---------------- GUI ----------------
if __name__ == "__main__":
    root = tk.Tk()
    root.title("EPUB 转 TXT 小工具")

    info = tk.Label(root, text="按目录分章节转换 EPUB → TXT\n清理脚注/符号，统一段落格式", wraplength=360)
    info.pack(pady=10)

    btn = tk.Button(root, text="选择 EPUB 转换", command=start_conversion,
                    width=30, height=2, bg="lightblue")
    btn.pack(pady=20)
    root.mainloop()