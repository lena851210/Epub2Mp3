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
import math
import re

CONFIG_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "config.json")

# 停顿时间设置（秒） - 直接在代码中配置
PAUSE_SETTINGS = {
    "title_pause": 3.0,    # 标题后的停顿时间
    "paragraph_pause": 2.0 # 段落间的停顿时间
}

# 音色描述映射（中文名称 -> Edge TTS名称）
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

# 基准朗读速度（字/分钟）- 1.0倍速下的标准值
BASE_WORDS_PER_MINUTE = 300

def load_config():
    default_config = {
        "edge": {
            "voice_name": "zh-CN-XiaoxiaoNeural",
            "speed": 1.0,
            "pitch": 0,
            "volume": 0
        },
        "last_txt_dir": "",
        "merge_audio": True,
        "target_duration": 40,
        "words_per_minute": BASE_WORDS_PER_MINUTE  # 使用基准值作为默认
    }
    if os.path.exists(CONFIG_FILE):
        with open(CONFIG_FILE, "r", encoding="utf-8") as f:
            cfg = json.load(f)
            # 确保所有默认键都存在
            if "edge" not in cfg: cfg["edge"] = {}
            for key, value in default_config["edge"].items():
                if key not in cfg["edge"]:
                    cfg["edge"][key] = value
            # 确保新增配置项存在
            for key, value in default_config.items():
                if key not in cfg:
                    cfg[key] = value
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
        if not sentence.strip(): continue
        test = current + sentence
        if len(test) <= max_length:
            current = test
        else:
            if current: paras.append(current)
            current = sentence
    if current: paras.append(current)
    return paras

def sanitize_filename(filename):
    """移除文件名中的非法字符"""
    return re.sub(r'[<>:"/\\|?*]', '', filename)

class EdgeTTSWrapper:
    def __init__(self):
        self.voices = []
        self._load_voices_blocking()
        threading.Thread(target=self._load_voices_async, daemon=True).start()

    def _load_voices_blocking(self):
        try:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            voices = loop.run_until_complete(edge_tts.list_voices())
            # 只保留中文语音
            chinese_voices = [v for v in voices if v["Locale"].startswith("zh")]
            # 使用预设的中文名称
            self.voices = list(VOICE_MAPPING.keys())
            loop.close()
        except Exception as e:
            print("获取声音列表失败:", e)
            # 失败时使用预设的语音列表
            self.voices = list(VOICE_MAPPING.keys())

    def _load_voices_async(self):
        try:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            voices = loop.run_until_complete(edge_tts.list_voices())
            # 只保留中文语音
            chinese_voices = [v for v in voices if v["Locale"].startswith("zh")]
            # 使用预设的中文名称
            allv = list(VOICE_MAPPING.keys())
            if allv: self.voices = allv
        except Exception as e:
            print("异步加载声音失败:", e)
            # 失败时使用预设的语音列表
            self.voices = list(VOICE_MAPPING.keys())
            
    def refresh_voices(self):
        """刷新音色列表"""
        self._load_voices_blocking()
        return self.voices

    def text_to_speech(self, text, voice, speed, pitch, volume, output_file):
        # 将中文语音名称转换为Edge TTS名称
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
        # 调整窗口尺寸
        self.root.geometry("650x580")  # 稍微调整宽度以容纳刷新按钮
        self.stop_flag = False
        self.create_ui()

    def create_ui(self):
        main = ttk.Frame(self.root, padding="15")
        main.grid(row=0, column=0, sticky="nsew")
        self.root.columnconfigure(0, weight=1)
        self.root.rowconfigure(0, weight=1)
        
        # 为蓝色按钮创建样式
        style = ttk.Style(self.root)
        style.configure("Accent.TButton", foreground="black", background="#0078D7", padding=6, font=('Helvetica', 12))
        
        # ===== 设置区域 (无外框) =====
        settings_frame = ttk.Frame(main, padding=(0, 0, 0, 0))
        settings_frame.grid(row=0, column=0, sticky="ew")
        settings_frame.columnconfigure(0, weight=1)

        # 音色 + 试听 + 刷新
        voice_frame = ttk.Frame(settings_frame)
        voice_frame.pack(fill="x", pady=5)
        ttk.Label(voice_frame, text="音色:").pack(side="left", padx=(0, 10))
        
        # 获取当前配置的音色对应的中文名称
        current_voice_display = None
        for display_name, edge_name in VOICE_MAPPING.items():
            if edge_name == self.config["edge"]["voice_name"]:
                current_voice_display = display_name
                break
        if current_voice_display is None:
            current_voice_display = "晓晓(女)"
            
        self.voice_var = tk.StringVar(value=current_voice_display)
        self.voice_combo = ttk.Combobox(voice_frame, textvariable=self.voice_var, values=self.edge.voices, width=30, state="readonly")
        self.voice_combo.pack(side="left", padx=5, expand=True, fill="x")
        
        # 添加刷新按钮
        self.refresh_button = ttk.Button(voice_frame, text="刷新列表", command=self.refresh_voices, width=8)
        self.refresh_button.pack(side="left", padx=(5, 5))
        
        self.preview_button = ttk.Button(voice_frame, text="试听", command=self.preview_audio, width=8)
        self.preview_button.pack(side="left", padx=(0, 0))

        # 语速/音调/音量 一行
        controls_frame = ttk.Frame(settings_frame)
        controls_frame.pack(fill="x", pady=20)
        controls_frame.columnconfigure(0, weight=1)
        controls_frame.columnconfigure(1, weight=1)
        controls_frame.columnconfigure(2, weight=1)

        def make_slider(parent, label_text, var, from_, to, fmt):
            frame = ttk.Frame(parent)
            label_var = tk.StringVar(value=f"{label_text}: {fmt.format(var.get())}")
            label = ttk.Label(frame, textvariable=label_var, width=15, anchor="center")
            label.pack(fill="x")
            
            scale = ttk.Scale(frame, from_=from_, to=to, variable=var, orient="horizontal")
            scale.pack(fill="x", padx=5)
            
            var.trace_add("write", lambda *args: label_var.set(f"{label_text}: {fmt.format(var.get())}"))
            return frame

        self.speed_var = tk.DoubleVar(value=self.config["edge"]["speed"])
        speed_frame = make_slider(controls_frame, "语速", self.speed_var, 0.5, 2.0, "{:.2f}x")
        speed_frame.grid(row=0, column=0, sticky="ew")
        
        # 添加语速变化监听，自动更新估算字数
        def update_words_per_minute(*args):
            estimated_wpm = int(BASE_WORDS_PER_MINUTE * self.speed_var.get())
            self.wpm_var.set(estimated_wpm)
            self.wpm_label_var.set(f"估算字数: {estimated_wpm}字/分钟")
        
        self.speed_var.trace_add("write", update_words_per_minute)

        self.pitch_var = tk.DoubleVar(value=self.config["edge"]["pitch"])
        make_slider(controls_frame, "音调", self.pitch_var, -50, 50, "{:+.0f}Hz").grid(row=0, column=1, sticky="ew")
        self.volume_var = tk.DoubleVar(value=self.config["edge"]["volume"])
        make_slider(controls_frame, "音量", self.volume_var, -100, 100, "{:+.0f}%").grid(row=0, column=2, sticky="ew")

        # ===== 音频合并选项 =====
        merge_frame = ttk.Frame(settings_frame)
        merge_frame.pack(fill="x", pady=(10, 5))
        
        self.merge_var = tk.BooleanVar(value=self.config.get("merge_audio", True))
        merge_check = ttk.Checkbutton(merge_frame, text="合并音频为长段落", variable=self.merge_var, 
                                     command=self.toggle_merge_options)
        merge_check.pack(side="left", padx=(0, 10))
        
        # 目标时长设置
        ttk.Label(merge_frame, text="目标时长(分钟):").pack(side="left", padx=(0, 5))
        self.target_duration_var = tk.IntVar(value=self.config.get("target_duration", 40))
        self.target_duration_spin = ttk.Spinbox(merge_frame, from_=10, to=120, width=5, 
                                               textvariable=self.target_duration_var)
        self.target_duration_spin.pack(side="left", padx=(0, 10))
        
        # 估算字数显示（只读，自动计算）
        self.wpm_var = tk.IntVar(value=self.config.get("words_per_minute", BASE_WORDS_PER_MINUTE))
        self.wpm_label_var = tk.StringVar(value=f"估算字数: {self.wpm_var.get()}字/分钟")
        wpm_label = ttk.Label(merge_frame, textvariable=self.wpm_label_var)
        wpm_label.pack(side="left", padx=(0, 5))
        
        # 初始状态设置
        self.toggle_merge_options()

        # TXT 目录
        dir_frame = ttk.Frame(settings_frame)
        dir_frame.pack(fill="x", pady=(10, 5))
        ttk.Label(dir_frame, text="TXT目录:").pack(side="left", padx=(0, 5))
        self.txt_dir = tk.StringVar(value=self.config.get("last_txt_dir", ""))
        dir_entry = ttk.Entry(dir_frame, textvariable=self.txt_dir)
        dir_entry.pack(side="left", expand=True, fill="x", padx=5)
        ttk.Button(dir_frame, text="浏览...", command=self.select_input_dir).pack(side="left")

        # ===== 操作按钮 =====
        button_frame = ttk.Frame(main)
        button_frame.grid(row=2, column=0, pady=20, sticky="ew")
        button_frame.columnconfigure(1, weight=1) 

        ttk.Button(button_frame, text="停止", command=self.stop_generation, width=8).grid(row=0, column=0, sticky="w")
        ttk.Button(button_frame, text="打开音频目录 📁", command=self.open_output_dir, width=13).grid(row=0, column=2, sticky="e", padx=10)
        ttk.Button(button_frame, text="开始转换 🚀", command=self.start_generation, width=13).grid(row=0, column=3, sticky="e")

        # ===== 进度与日志 =====
        progress_frame = ttk.Frame(main)
        progress_frame.grid(row=3, column=0, sticky="ew")
        progress_frame.columnconfigure(0, weight=1)
        self.progress_var = tk.DoubleVar(value=0)
        self.progress_bar = ttk.Progressbar(progress_frame, variable=self.progress_var, maximum=100)
        self.progress_bar.grid(row=0, column=0, sticky="ew")

        # 日志区域
        log_frame = ttk.Frame(main)
        log_frame.grid(row=4, column=0, sticky="nsew", pady=(10, 0))
        log_frame.columnconfigure(0, weight=1)
        log_frame.rowconfigure(0, weight=1)
        main.rowconfigure(4, weight=1)

        self.log = tk.Text(log_frame, height=8, wrap="word", relief="solid", borderwidth=1)
        scrollbar = ttk.Scrollbar(log_frame, orient="vertical", command=self.log.yview)
        self.log.configure(yscrollcommand=scrollbar.set)
        self.log.grid(row=0, column=0, sticky="nsew")
        scrollbar.grid(row=0, column=1, sticky="ns")

        # 初始化一次估算字数
        update_words_per_minute()

        self.root.protocol("WM_DELETE_WINDOW", self.on_closing)

    def refresh_voices(self):
        """刷新音色列表"""
        def refresh_task():
            self.log_message("正在刷新音色列表...")
            voices = self.edge.refresh_voices()
            # 在主线程更新下拉菜单
            self.root.after(0, lambda: self.voice_combo.configure(values=voices))
            self.root.after(0, lambda: self.log_message(f"音色列表已刷新，共找到 {len(voices)} 个音色"))
            
        threading.Thread(target=refresh_task, daemon=True).start()

    def toggle_merge_options(self):
        # 根据是否选择合并音频来启用/禁用相关选项
        state = "normal" if self.merge_var.get() else "disabled"
        self.target_duration_spin.configure(state=state)

    def log_message(self, msg):
        self.root.after(0, lambda: self.log.insert("end", f"[{time.strftime('%H:%M:%S')}] {msg}\n"))
        self.root.after(0, lambda: self.log.see("end"))

    def select_input_dir(self):
        last_dir = self.txt_dir.get()
        # 修改：直接使用上次目录（如果有效），否则使用用户主目录
        initial_dir = last_dir if last_dir and os.path.isdir(last_dir) else os.path.expanduser("~")
        p = filedialog.askdirectory(initialdir=initial_dir)
        if p:
            self.txt_dir.set(p)

    def preview_audio(self):
        """试听在后台线程运行，使用 afplay 避免崩溃"""
        def worker():
            # 使用选择的音色名称生成试听文本
            voice_name = self.voice_var.get()
            text = f"你好，我是你的有声书助手，现在是{voice_name}为您朗读。"
            
            tmp_file = None
            try:
                with tempfile.NamedTemporaryFile(delete=False, suffix=".mp3") as tmp:
                    tmp_file = tmp.name
                
                # 直接使用普通文本，不使用SSML
                self.edge.text_to_speech(
                    text=text, voice=self.voice_var.get(),
                    speed=self.speed_var.get(), pitch=self.pitch_var.get(),
                    volume=self.volume_var.get(), output_file=tmp_file
                )
                
                self.log_message("试听文件生成成功，开始播放...")
                if platform.system() == "Darwin":
                    subprocess.run(["afplay", tmp_file], check=True, capture_output=True)
                elif platform.system() == "Windows":
                    proc = subprocess.Popen(["start", "/wait", tmp_file], shell=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
                    proc.wait()
                else: # Linux
                    subprocess.run(["xdg-open", tmp_file], check=True)

                self.log_message("试听播放完成。")
                
            except FileNotFoundError:
                 self.log_message(f"播放失败: 系统命令未找到。请确保 'afplay' (macOS) 或相应播放器可用。")
            except subprocess.CalledProcessError as e:
                self.log_message(f"播放命令执行失败: {e.stderr.decode()}")
            except Exception as e:
                self.log_message(f"试听失败: {e}")
            finally:
                if tmp_file and os.path.exists(tmp_file): os.remove(tmp_file)
        
        threading.Thread(target=worker, daemon=True).start()

    def start_generation(self):
        self.stop_flag = False
        self.log_message("开始转换任务...")
        threading.Thread(target=self.generate, daemon=True).start()

    def stop_generation(self):
        self.stop_flag = True
        self.log_message("用户请求停止生成...")

    def open_output_dir(self):
        txt_dir = self.txt_dir.get()
        if not txt_dir or not os.path.isdir(txt_dir):
            messagebox.showerror("错误", "请先选择有效的TXT目录")
            return
        out_dir = os.path.join(txt_dir, "Audio")
        os.makedirs(out_dir, exist_ok=True)
        if platform.system() == "Darwin": os.system(f'open "{out_dir}"')
        elif platform.system() == "Windows": os.startfile(out_dir)
        else: os.system(f'xdg-open "{out_dir}"')

    def on_closing(self):
        self.stop_flag = True
        # 将中文语音名称转换为Edge TTS名称保存
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
        """估算文本朗读时长（分钟）"""
        words_per_minute = self.wpm_var.get()
        word_count = len(text)
        return word_count / words_per_minute

    def generate(self):
        txt_dir = self.txt_dir.get()
        if not txt_dir or not os.path.isdir(txt_dir):
            self.root.after(0, lambda: messagebox.showerror("错误", "请选择有效的TXT目录"))
            return
        out_dir = os.path.join(txt_dir, "Audio")
        os.makedirs(out_dir, exist_ok=True)

        files = [f for f in sorted(os.listdir(txt_dir)) if f.lower().endswith(".txt")]
        total = len(files)
        if not files:
            self.log_message("目录下未找到TXT文件")
            return

        if not self.merge_var.get():
            self.generate_single_files(files, txt_dir, out_dir, total)
        else:
            self.generate_merged_files(files, txt_dir, out_dir, total)

    def generate_single_files(self, files, txt_dir, out_dir, total):
        """原逻辑：每个TXT文件生成一个MP3"""
        for i, f in enumerate(files, 1):
            if self.stop_flag: break
            ipath = os.path.join(txt_dir, f)
            opath = os.path.join(out_dir, os.path.splitext(f)[0] + ".mp3")
            self.progress_var.set(i / total * 100)
            
            if os.path.exists(opath):
                self.log_message(f"跳过已存在: {f}")
                continue

            with open(ipath, "r", encoding="utf-8") as fin:
                text = fin.read().strip()
            
            # 使用普通文本，不使用SSML
            paras = preprocess_text(text)
            tempfiles = []
            
            for j, p in enumerate(paras):
                if self.stop_flag: break
                tfile = os.path.join(out_dir, f"temp_{i}_{j}.mp3")
                try:
                    self.edge.text_to_speech(
                        text=p, voice=self.voice_var.get(),
                        speed=self.speed_var.get(), pitch=self.pitch_var.get(),
                        volume=self.volume_var.get(), output_file=tfile
                    )
                    tempfiles.append(tfile)
                    self.log_message(f"[{i}/{total}] {f}: 第{j+1}/{len(paras)}段生成完成")
                except Exception as e:
                    self.log_message(f"[{i}/{total}] {f}: 第{j+1}段失败: {e}")

            if tempfiles and not self.stop_flag:
                combined = AudioSegment.empty()
                for tf in tempfiles:
                    try:
                        audio = AudioSegment.from_file(tf, format="mp3")
                        combined += audio
                        # 在段落之间添加静音间隔
                        combined += AudioSegment.silent(duration=1000)  # 1秒静音
                        os.remove(tf)
                    except Exception as e:
                        self.log_message(f"合并文件时错误: {e}")
                
                try:
                    combined.export(opath, format="mp3")
                    self.log_message(f"生成完成: {os.path.basename(opath)}")
                except Exception as e:
                    self.log_message(f"导出文件失败: {e}")

        self.progress_var.set(100)
        self.log_message("所有任务处理完成。")
        if self.stop_flag:
            self.log_message("任务被用户中断。")

    def generate_merged_files(self, files, txt_dir, out_dir, total):
        """新逻辑：按目标时长合并多个TXT文件"""
        target_duration = self.target_duration_var.get()  # 分钟
        part_num = 1
        current_text = ""
        current_duration = 0
        current_files = []  # 记录当前合并块包含的文件
        
        for i, f in enumerate(files, 1):
            if self.stop_flag: break
            
            ipath = os.path.join(txt_dir, f)
            with open(ipath, "r", encoding="utf-8") as fin:
                text = fin.read().strip()
            
            # 估算当前文本时长
            file_duration = self.estimate_duration(text)
            
            # 如果当前文本本身就很长，超过目标时长，单独处理
            if file_duration >= target_duration:
                # 先处理之前积累的文本
                if current_text:
                    self.process_text_chunk(current_text, out_dir, part_num, current_files)
                    part_num += 1
                    current_text = ""
                    current_duration = 0
                    current_files = []
                
                # 处理这个长文件
                opath = self.generate_output_filename(out_dir, part_num, [f])
                self.process_single_file(text, opath, [f])
                part_num += 1
                continue
            
            # 如果加上当前文本不会超过目标时长，则添加到当前积累
            if current_duration + file_duration <= target_duration:
                current_text += "\n\n" + text if current_text else text
                current_duration += file_duration
                current_files.append(f)
            else:
                # 当前积累已接近目标时长，先处理积累的文本
                if current_text:
                    self.process_text_chunk(current_text, out_dir, part_num, current_files)
                    part_num += 1
                
                # 重置积累，从当前文件开始
                current_text = text
                current_duration = file_duration
                current_files = [f]
            
            # 更新进度条
            self.progress_var.set(i / total * 100)
        
        # 处理最后剩余的文本
        if current_text and not self.stop_flag:
            self.process_text_chunk(current_text, out_dir, part_num, current_files)
        
        self.progress_var.set(100)
        self.log_message("所有任务处理完成。")
        if self.stop_flag:
            self.log_message("任务被用户中断。")

    def generate_output_filename(self, out_dir, part_num, file_list):
        """生成输出文件名，基于包含的文件"""
        if len(file_list) == 1:
            # 单个文件
            base_name = os.path.splitext(file_list[0])[0]
            safe_name = sanitize_filename(base_name)
            return os.path.join(out_dir, f"{safe_name}.mp3")
        else:
            # 多个文件
            first_file = os.path.splitext(file_list[0])[0]
            last_file = os.path.splitext(file_list[-1])[0]
            first_safe = sanitize_filename(first_file)
            last_safe = sanitize_filename(last_file)
            return os.path.join(out_dir, f"{first_safe}_到_{last_safe}.mp3")

    def process_text_chunk(self, text, out_dir, part_num, file_list):
        """处理一个文本块并生成MP3"""
        opath = self.generate_output_filename(out_dir, part_num, file_list)
        if os.path.exists(opath):
            self.log_message(f"跳过已存在: {os.path.basename(opath)}")
            return
            
        file_names = ", ".join([os.path.splitext(f)[0] for f in file_list])
        self.log_message(f"开始生成合并段落 {part_num}: 包含 {file_names}")
        
        # 使用普通文本，不使用SSML
        paras = preprocess_text(text)
        tempfiles = []
        
        for j, p in enumerate(paras):
            if self.stop_flag: break
            tfile = os.path.join(out_dir, f"temp_part{part_num}_{j}.mp3")
            try:
                self.edge.text_to_speech(
                    text=p, voice=self.voice_var.get(),
                    speed=self.speed_var.get(), pitch=self.pitch_var.get(),
                    volume=self.volume_var.get(), output_file=tfile
                )
                tempfiles.append(tfile)
                self.log_message(f"段落 {part_num}: 第{j+1}/{len(paras)}段生成完成")
            except Exception as e:
                self.log_message(f"段落 {part_num}: 第{j+1}段失败: {e}")

        if tempfiles and not self.stop_flag:
            combined = AudioSegment.empty()
            for tf in tempfiles:
                try:
                    audio = AudioSegment.from_file(tf, format="mp3")
                    combined += audio
                    # 在段落之间添加静音间隔
                    combined += AudioSegment.silent(duration=1000)  # 1秒静音
                    os.remove(tf)
                except Exception as e:
                    self.log_message(f"合并文件时错误: {e}")
            
            try:
                combined.export(opath, format="mp3")
                actual_duration = len(combined) / 60000  # 转换为分钟
                self.log_message(f"生成完成: {os.path.basename(opath)} (时长: {actual_duration:.1f}分钟)")
            except Exception as e:
                self.log_message(f"导出文件失败: {e}")

    def process_single_file(self, text, output_path, file_list):
        """处理单个文件（长文件单独处理）"""
        if os.path.exists(output_path):
            self.log_message(f"跳过已存在: {os.path.basename(output_path)}")
            return
            
        file_name = os.path.splitext(file_list[0])[0] if file_list else "未知文件"
        self.log_message(f"开始处理 {file_name}")
        
        # 使用普通文本，不使用SSML
        paras = preprocess_text(text)
        tempfiles = []
        
        for j, p in enumerate(paras):
            if self.stop_flag: break
            tfile = os.path.join(os.path.dirname(output_path), f"temp_long_{j}.mp3")
            try:
                self.edge.text_to_speech(
                    text=p, voice=self.voice_var.get(),
                    speed=self.speed_var.get(), pitch=self.pitch_var.get(),
                    volume=self.volume_var.get(), output_file=tfile
                )
                tempfiles.append(tfile)
                self.log_message(f"{file_name}: 第{j+1}/{len(paras)}段生成完成")
            except Exception as e:
                self.log_message(f"{file_name}: 第{j+1}段失败: {e}")

        if tempfiles and not self.stop_flag:
            combined = AudioSegment.empty()
            for tf in tempfiles:
                try:
                    audio = AudioSegment.from_file(tf, format="mp3")
                    combined += audio
                    # 在段落之间添加静音间隔
                    combined += AudioSegment.silent(duration=1000)  # 1秒静音
                    os.remove(tf)
                except Exception as e:
                    self.log_message(f"合并文件时错误: {e}")
            
            try:
                combined.export(output_path, format="mp3")
                actual_duration = len(combined) / 60000  # 转换为分钟
                self.log_message(f"生成完成: {os.path.basename(output_path)} (时长: {actual_duration:.1f}分钟)")
            except Exception as e:
                self.log_message(f"导出文件失败: {e}")

    def run(self):
        self.root.mainloop()

if __name__ == "__main__":
    AudiobookGenerator().run()