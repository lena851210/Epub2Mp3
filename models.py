# -*- coding: utf-8 -*-
"""
模块1：配置管理、字数估算、TTS、文本处理工具
"""

import os
import re
import json
import threading
import unicodedata
import asyncio
from typing import Dict, Any, List, Tuple

from bs4 import BeautifulSoup, NavigableString, Tag
import edge_tts

# ====== 常量定义 ======
BASE_WORDS_PER_MINUTE = 300

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

# HTML 标签分类
BLOCK_TAGS = ("h1","h2","h3","h4","h5","h6","p","li","dd","dt","blockquote","pre","article","section","div")
INLINE_UNWRAP = ("a","span","em","strong","b","i","u","font","mark","small","sub","sup","code","kbd","s")
REMOVE_TAGS = ("script","style","header","footer","nav","aside","figure","figcaption","table","svg","img","video","audio","noscript")
NOISE_KEYWORDS = ("footnote","note","noteref","citation","cite","ref","xref","header","footer","copyright","pagebreak","page-num","toc","index")


# ====== 【Class 1】配置管理器 ======
class ConfigManager:
    """管理程序配置文件"""
    def __init__(self, config_file: str):
        self.config_file = config_file
        self._lock = threading.RLock()           # 🟢 先初始化这个
        self._auto_save_timer = None
        self._dirty = False
        self.config = self._load()               # 🟢 再调用这个

    def _load(self) -> Dict[str, Any]:
        """加载配置文件"""
        default = {
            "edge": {"voice_name": "zh-CN-XiaoxiaoNeural", "speed": 1.0, "pitch": 0, "volume": 0},
            "last_txt_dir": "",
            "merge_audio": True,
            "target_duration": 40,
            "words_per_minute": BASE_WORDS_PER_MINUTE
        }
        if os.path.exists(self.config_file):
            try:
                with open(self.config_file, "r", encoding="utf-8") as f:
                    cfg = json.load(f)
                    for key in default:
                        if key not in cfg:
                            cfg[key] = default[key]
                    return cfg
            except Exception:
                return default
        else:
            self._save(default)
            return default

    def _save(self, cfg: Dict = None):
        """保存配置文件"""
        try:
            with self._lock:
                with open(self.config_file, "w", encoding="utf-8") as f:
                    json.dump(cfg or self.config, f, ensure_ascii=False, indent=2)
        except Exception as e:
            print(f"配置保存失败: {e}")

    def get(self, key: str, default=None):
        """获取配置值"""
        with self._lock:
            return self.config.get(key, default)

    def set(self, key: str, value: Any):
        """设置配置值"""
        with self._lock:
            self.config[key] = value
            self._dirty = True
        self._schedule_save()

    def _schedule_save(self):
        """延迟保存（2秒后）"""
        with self._lock:
            if self._auto_save_timer:
                try:
                    self._auto_save_timer.cancel()
                except Exception:
                    pass
            self._auto_save_timer = threading.Timer(2.0, self._flush)
            self._auto_save_timer.daemon = True
            self._auto_save_timer.start()

    def _flush(self):
        """立即保存"""
        with self._lock:
            if self._dirty:
                self._save()
                self._dirty = False

    def flush(self):
        """手动强制保存"""
        self._flush()


# ====== 【Class 2】字数估算器 ======
class DurationEstimator:
    """估算文本朗读时长"""
    def __init__(self, base_wpm: int = BASE_WORDS_PER_MINUTE):
        self.base_wpm = base_wpm
        self.cache = {}
        self._lock = threading.RLock()

    def count_chars(self, text: str) -> int:
        """计算文本字数（去除空白）"""
        return len(text.replace(" ", "").replace("\n", "").replace("\t", ""))

    def estimate_seconds(self, chars: int, wpm: int) -> int:
        """估算秒数"""
        effective_wpm = max(1, wpm)
        return int(round(chars / effective_wpm * 60))

    def cache_file_chars(self, file_path: str, text: str) -> int:
        """缓存文件字数"""
        chars = self.count_chars(text)
        with self._lock:
            self.cache[file_path] = chars
        return chars

    def get_cached_chars(self, file_path: str) -> int:
        """获取缓存的字数"""
        with self._lock:
            return self.cache.get(file_path, 0)

    def clear_cache(self):
        """清除缓存"""
        with self._lock:
            self.cache.clear()


# ====== 【Class 3】TTS 包装器 ======
class EdgeTTSWrapper:
    """Edge TTS 语音合成包装"""
    def __init__(self):
        self.voices = []
        self._load_voices_blocking()
        threading.Thread(target=self._load_voices_async, daemon=True).start()

    def _load_voices_blocking(self):
        """同步加载声音列表"""
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
        """异步加载声音列表"""
        try:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            _ = loop.run_until_complete(edge_tts.list_voices())
            self.voices = list(VOICE_MAPPING.keys())
        except Exception as e:
            print("异步加载声音失败:", e)
            self.voices = list(VOICE_MAPPING.keys())

    def refresh_voices(self):
        """刷新声音列表"""
        self._load_voices_blocking()
        return self.voices

    def _resolve_voice_code(self, voice: str) -> str:
        """允许 voice 既可以是 UI 标签，也可以直接是 zh-CN-xxxNeural 代码"""
        if not voice:
            return VOICE_MAPPING.get("晓晓(女)", "zh-CN-XiaoxiaoNeural")
        # 1) UI 标签（例如：晓晓(女)）
        if voice in VOICE_MAPPING:
            return VOICE_MAPPING[voice]
        # 2) 直接传入的 voice code（例如：zh-CN-XiaoxiaoNeural）
        if voice in VOICE_MAPPING.values():
            return voice
        if isinstance(voice, str) and re.match(r"^[a-z]{2}-[A-Z]{2}-", voice):
            return voice
        return VOICE_MAPPING.get("晓晓(女)", "zh-CN-XiaoxiaoNeural")

    def text_to_speech(self, text: str, voice: str, speed: float, pitch: float, volume: float, output_file: str):
        """文本转语音"""
        edge_voice = self._resolve_voice_code(voice)

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
        """文本转语音"""
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


# ====== 文本处理工具函数 ======

def normalize_whitespace(text: str) -> str:
    """规范化空白字符"""
    text = re.sub(r"[\u00AD\u200B-\u200F\u2028\u2029\uFEFF]", "", text)
    text = text.replace("\xa0", " ")
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\r\n", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def safe_node_attrs(node: Any) -> str:
    """安全获取节点属性"""
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
    """判断节点是否为噪音（脚注等）"""
    attrs = safe_node_attrs(node)
    return any(k in attrs for k in NOISE_KEYWORDS)


def prepare_soup(html: str) -> BeautifulSoup:
    """清理 HTML 并准备 BeautifulSoup"""
    soup = BeautifulSoup(html, "lxml")
    
    # 删除不需要的标签
    for t in list(soup.find_all(REMOVE_TAGS)):
        try:
            t.decompose()
        except Exception:
            pass
    
    # 删除噪音节点
    for t in list(soup.find_all(True)):
        try:
            if looks_like_noise(t):
                t.decompose()
            elif t.get('role') in ['note', 'comment', 'footnote']:
                t.decompose()
            elif any(keyword in (t.get('class') or []) for keyword in ['note', 'footnote', 'annotation', 'comment']):
                t.decompose()
        except Exception:
            continue
    
    # 转换 br 为换行
    for br in soup.find_all("br"):
        try:
            br.replace_with(NavigableString("\n"))
        except Exception:
            pass
    
    # 展开内联标签
    for tagname in INLINE_UNWRAP:
        for t in list(soup.find_all(tagname)):
            try:
                t.unwrap()
            except Exception:
                pass
    
    # 删除引用标记
    text_content = str(soup)
    text_content = re.sub(r'【\d+】|\[\d+\]|\(\d+\)|<\d+>', '', text_content)
    soup = BeautifulSoup(text_content, "lxml")
    
    return soup


def extract_title_from_soup(soup: BeautifulSoup) -> str:
    """从 BeautifulSoup 中提取标题"""
    for tag in ("h1","h2","h3","h4","h5","h6"):
        el = soup.find(tag)
        if el:
            title_text = " ".join(el.stripped_strings)
            if title_text:
                title_text = re.sub(r'^\d+\s*', '', title_text).strip()
                if title_text:
                    return title_text
    
    title_el = soup.find("title")
    if title_el:
        title_text = " ".join(title_el.stripped_strings)
        if title_text:
            title_text = re.sub(r'^\d+\s*', '', title_text).strip()
            return title_text
    
    return ""


def text_of(el: Tag) -> str:
    """提取元素的文本"""
    try:
        return " ".join(s.strip() for s in el.stripped_strings)
    except Exception:
        try:
            return "".join([str(x) for x in el.stripped_strings])
        except Exception:
            return ""


def soup_to_paragraphs(soup: BeautifulSoup) -> str:
    """将 BeautifulSoup 转换为段落文本"""
    body = soup.body if soup.body else soup
    
 
    # 临时：不移除标题标签（h1~h6）
    # for tag in ("h1","h2","h3","h4","h5","h6"):
    #     for h in body.find_all(tag):
    #         h.decompose()
    
    paras: List[str] = []
    for blk in list(body.find_all(BLOCK_TAGS)):
        try:
            if any(parent.name in BLOCK_TAGS for parent in getattr(blk, "parents", [])):
                continue
        except Exception:
            pass
        t = text_of(blk)
        if t:
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


def clean_text_from_html_bytes(html_bytes: bytes) -> Tuple[str, str]:
    """从 HTML 字节中清理文本"""
    html_str = html_bytes.decode("utf-8", errors="ignore")
    soup = prepare_soup(html_str)
    title = extract_title_from_soup(soup)
    text = soup_to_paragraphs(soup)
    text = merge_broken_short_lines(text)
    text = normalize_whitespace(text)
    return title, text


def merge_broken_short_lines(text: str) -> str:
    """合并断裂的短行"""
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


def sanitize_filename(name: str, max_len: int = 80) -> str:
    """清理文件名"""
    if not name:
        return "无标题章节"
    name = unicodedata.normalize("NFKC", str(name)).strip()
    name = re.sub(r'[\\/:*?"<>|\n\r\t]+', "", name)
    name = re.sub(r"\s+", " ", name)
    if len(name) > max_len:
        name = name[:max_len].rstrip()
    return name or "无标题章节"