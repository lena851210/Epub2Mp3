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
BASE_WORDS_PER_MINUTE = 300  # 基准：1.0x 约 300 字/分钟（中文按"字"计）

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
        "words_per_minute": BASE_WORDS_PER_MINUTE,
        "enable_text_preprocessing": True  # 新增配置项：是否启用文本预处理
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

def sanitize_filename(filename):
    return re.sub(r'[<>:"/\\|?*]', '', filename)

# 新增函数：文本预处理，使TTS朗读更自然
def preprocess_text_for_tts(text):
    """
    预处理文本，使TTS能够更自然地朗读数字、百分比等特殊内容
    """
    if not text:
        return text
    
    # 处理百分比
    text = re.sub(r'(\d+)%', lambda m: f'{number_to_chinese(m.group(1))}百分之', text)
    
    # 处理年份（四位数）
    text = re.sub(r'(\d{4})年', lambda m: f'{number_to_chinese_year(m.group(1))}年', text)
    
    # 处理小数
    text = re.sub(r'(\d+)\.(\d+)', lambda m: f'{number_to_chinese(m.group(1))}点{number_to_chinese(m.group(2))}', text)
    
    # 处理分数
    text = re.sub(r'(\d+)/(\d+)', lambda m: f'{number_to_chinese(m.group(1))}分之{number_to_chinese(m.group(2))}', text)
    
    # 处理货币金额（简单处理）
    text = re.sub(r'¥(\d+(?:\.\d+)?)', lambda m: f'{number_to_chinese_currency(m.group(1))}元', text)
    text = re.sub(r'\$(\d+(?:\.\d+)?)', lambda m: f'{number_to_chinese_currency(m.group(1))}美元', text)
    
    # 处理温度
    text = re.sub(r'(\d+)°C', lambda m: f'{number_to_chinese(m.group(1))}摄氏度', text)
    text = re.sub(r'(\d+)°F', lambda m: f'{number_to_chinese(m.group(1))}华氏度', text)
    
    # 处理电话号码（简单处理）
    text = re.sub(r'(\d{3,4})-(\d{3,4})-(\d{3,4})', 
                 lambda m: f'{number_to_chinese_digit(m.group(1))} - {number_to_chinese_digit(m.group(2))} - {number_to_chinese_digit(m.group(3))}', text)
    
    return text

# 辅助函数：将数字转换为中文读法
def number_to_chinese(num_str):
    """将数字转换为中文读法（用于普通数字）"""
    num_map = {
        '0': '零', '1': '一', '2': '二', '3': '三', '4': '四',
        '5': '五', '6': '六', '7': '七', '8': '八', '9': '九'
    }
    
    # 简单实现，只处理0-99
    try:
        num = int(num_str)
        if num <= 10:
            chinese_nums = ['零', '一', '二', '三', '四', '五', '六', '七', '八', '九', '十']
            return chinese_nums[num]
        elif num < 20:
            return f'十{chinese_nums[num - 10]}'
        elif num < 100:
            tens = num // 10
            units = num % 10
            if units == 0:
                return f'{chinese_nums[tens]}十'
            else:
                return f'{chinese_nums[tens]}十{chinese_nums[units]}'
        else:
            # 对于大于99的数字，保持原样
            return num_str
    except:
        return num_str

def number_to_chinese_year(num_str):
    """将年份数字转换为中文读法"""
    if len(num_str) != 4:
        return num_str
    
    # 将每一位数字单独读出
    digits = [number_to_chinese_digit(d) for d in num_str]
    return ''.join(digits)

def number_to_chinese_digit(num_str):
    """将数字字符串逐位转换为中文读法"""
    num_map = {
        '0': '零', '1': '一', '2': '二', '3': '三', '4': '四',
        '5': '五', '6': '六', '7': '七', '8': '八', '9': '九'
    }
    
    result = []
    for char in num_str:
        if char in num_map:
            result.append(num_map[char])
        else:
            result.append(char)
    
    return ''.join(result)

def number_to_chinese_currency(num_str):
    """将货币金额转换为中文读法（简化版）"""
    try:
        # 处理整数部分
        if '.' in num_str:
            integer_part, decimal_part = num_str.split('.')
        else:
            integer_part, decimal_part = num_str, None
        
        integer_chinese = number_to_chinese(integer_part)
        
        if decimal_part:
            decimal_chinese = number_to_chinese_digit(decimal_part)
            return f'{integer_chinese}点{decimal_chinese}'
        else:
            return integer_chinese
    except:
        return num_str

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

    def text_to_speech(self, text, voice, speed, pitch, volume, output_file, enable_preprocessing=True):
        # 如果启用文本预处理，则先处理文本
        if enable_preprocessing:
            text = preprocess_text_for_tts(text)
        
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

        # 文本预处理功能已内置，无需保存配置
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

        # 表格 + 双滚动条（新增"预估时长"列）
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
        self.files_tree.bind("<MouseWheel>", lambda e: self.root.after_idle(self.refresh_tree_overlays()))
        self.files_tree.bind("<Button-4>", lambda e: self.root.after_idle(self.refresh_tree_overlays()))
        self.files_tree.bind("<Button-5>", lambda e: self.root.after_idle(self.refresh_tree_overlays()))
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
        # 根据当前WPM刷新"预估时长"列
        try:
            for iid in self.files_tree.get_children():
                chars = self.file_chars.get(iid, None)
                if chars is None:
                    # 从"字数"列读一次
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
            except Exception as e:
                self.set_status(f"试听失败: {e}")
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
        self.config["enable_text_preprocessing"] = self.preprocessing_var.get()  # 保存预处理设置
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
                            self.set_file_status(f, f"✅ 已完成（🕒{dur}）", spinning=False)
                        else:
                            self.set_file_status(f, "✅ 已完成", spinning=False)
                    except Exception as e:
                        self.set_file_status(f, "合并失败", spinning=False)
                        self.set_error(f, f"合并音频失败: {e}")
                        ok = False
                else:
                    # 多段合并
                    try:
                        combined = AudioSegment.empty()
                        for tfile in tempfiles:
                            combined += AudioSegment.from_mp3(tfile)
                        combined.export(opath, format="mp3")
                        self.set_file_progress(f, 100.0)
                        dur = self.get_mp3_duration_str(opath)
                        if dur:
                            self.set_file_status(f, f"✅ 已完成（🕒{dur}）", spinning=False)
                        else:
                            self.set_file_status(f, "✅ 已完成", spinning=False)
                    except Exception as e:
                        self.set_file_status(f, "合并失败", spinning=False)
                        self.set_error(f, f"合并音频失败: {e}")
                        ok = False

            # 清理临时文件
            for tfile in tempfiles:
                try:
                    if os.path.exists(tfile):
                        os.remove(tfile)
                except Exception:
                    pass

            if not ok:
                self.set_file_status(f, "失败", spinning=False)

    # ----- 合并文件输出 -----
    def generate_merged_files(self, files, txt_dir, out_dir):
        # 按目标时长分组文件
        target_duration = self.target_duration_var.get() * 60  # 转换为秒
        wpm = max(1, self.wpm_var.get())
        
        groups = []
        current_group = []
        current_chars = 0
        
        for f in files:
            chars = self.file_chars.get(f, 0)
            estimated_duration = chars / wpm * 60  # 估算时长（秒）
            
            if current_chars + chars > target_duration * 1.2 and current_group:  # 允许20%的误差
                groups.append(current_group)
                current_group = [f]
                current_chars = chars
            else:
                current_group.append(f)
                current_chars += chars
        
        if current_group:
            groups.append(current_group)
        
        for group_idx, group in enumerate(groups, 1):
            if self.stop_flag:
                break
                
            group_name = f"合并音频_{group_idx:02d}"
            opath = os.path.join(out_dir, group_name + ".mp3")
            
            if os.path.exists(opath):
                for f in group:
                    self.set_file_progress(f, 100.0)
                    dur = self.get_mp3_duration_str(opath)
                    if dur:
                        self.set_file_status(f, f"已存在(跳过)（🕒{dur}）", spinning=False)
                    else:
                        self.set_file_status(f, "已存在(跳过)", spinning=False)
                continue
            
            # 合并组内所有文件
            all_text = ""
            for f in group:
                if self.stop_flag:
                    break
                ipath = os.path.join(txt_dir, f)
                text = self.read_text_file(ipath)
                if text:
                    all_text += text.strip() + "\n\n"
            
            if not all_text.strip():
                for f in group:
                    self.set_file_status(f, "失败：无有效内容", spinning=False)
                continue
            
            # 预处理文本
            paras = preprocess_text(all_text.strip())
            total_paras = max(1, len(paras))
            
            for f in group:
                self.set_file_status(f, f"合并组 {group_idx}，共 {total_paras} 段", spinning=False)
            
            tempfiles = []
            ok = True
            
            for j, p in enumerate(paras, 1):
                if self.stop_flag:
                    ok = False
                    for f in group:
                        self.set_file_status(f, "已中断", spinning=False)
                    break
                
                tfile = os.path.join(out_dir, f"temp_group_{group_idx}_{j}.mp3")
                bn = os.path.basename(tfile)
                
                for f in group:
                    self.set_file_status(f, f"合成中 {bn} ({j}/{total_paras})", spinning=True)
                
                if not self.tts_with_retry(p, tfile, iid_for_error=group[0], max_retries=3):
                    ok = False
                    for f in group:
                        self.set_file_status(f, f"合成失败 {bn}", spinning=False)
                    break
                
                tempfiles.append(tfile)
                
                # 更新组内所有文件的进度
                progress = j / total_paras * 100.0
                for f in group:
                    self.set_file_progress(f, progress)
            
            if ok and not self.stop_flag:
                # 合并与导出
                for f in group:
                    self.set_file_status(f, f"合并音频（{len(tempfiles)} 段）", spinning=True)
                
                if len(tempfiles) == 1:
                    try:
                        shutil.move(tempfiles[0], opath)
                        for f in group:
                            self.set_file_progress(f, 100.0)
                            dur = self.get_mp3_duration_str(opath)
                            if dur:
                                self.set_file_status(f, f"✅ 已完成（🕒{dur}）", spinning=False)
                            else:
                                self.set_file_status(f, "✅ 已完成", spinning=False)
                    except Exception as e:
                        for f in group:
                            self.set_file_status(f, "合并失败", spinning=False)
                            self.set_error(f, f"合并音频失败: {e}")
                        ok = False
                else:
                    try:
                        combined = AudioSegment.empty()
                        for tfile in tempfiles:
                            combined += AudioSegment.from_mp3(tfile)
                        combined.export(opath, format="mp3")
                        for f in group:
                            self.set_file_progress(f, 100.0)
                            dur = self.get_mp3_duration_str(opath)
                            if dur:
                                self.set_file_status(f, f"✅ 已完成（🕒{dur}）", spinning=False)
                            else:
                                self.set_file_status(f, "✅ 已完成", spinning=False)
                    except Exception as e:
                        for f in group:
                            self.set_file_status(f, "合并失败", spinning=False)
                            self.set_error(f, f"合并音频失败: {e}")
                        ok = False
            
            # 清理临时文件
            for tfile in tempfiles:
                try:
                    if os.path.exists(tfile):
                        os.remove(tfile)
                except Exception:
                    pass
            
            if not ok:
                for f in group:
                    self.set_file_status(f, "失败", spinning=False)

if __name__ == "__main__":
    app = AudiobookGenerator()
    app.root.mainloop()