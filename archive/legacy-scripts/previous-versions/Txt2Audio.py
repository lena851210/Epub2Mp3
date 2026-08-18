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
from pydub import AudioSegment
import simpleaudio as sa

CONFIG_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "config.json")

# ... [ load_config, save_config, preprocess_text 等核心函数保持不变 ] ...
def load_config():
    default_config = {
        "edge": {
            "voice_name": "zh-CN-XiaoxiaoNeural",
            "speed": 1.0,
            "pitch": 0,
            "volume": 0
        },
        "last_txt_dir": ""
    }
    if os.path.exists(CONFIG_FILE):
        with open(CONFIG_FILE, "r", encoding="utf-8") as f:
            cfg = json.load(f)
            # 确保所有默认键都存在
            if "edge" not in cfg: cfg["edge"] = {}
            for key, value in default_config["edge"].items():
                if key not in cfg["edge"]:
                    cfg["edge"][key] = value
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
            self.voices = [v["ShortName"] for v in voices if v["Locale"].startswith("zh")]
            if not self.voices: self.voices = [v["ShortName"] for v in voices]
            loop.close()
        except Exception as e:
            print("获取声音列表失败:", e)

    def _load_voices_async(self):
        try:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            voices = loop.run_until_complete(edge_tts.list_voices())
            allv = [v["ShortName"] for v in voices if v["Locale"].startswith("zh")]
            if not allv: allv = [v["ShortName"] for v in voices]
            if allv: self.voices = allv
        except Exception as e:
            print("异步加载声音失败:", e)

    def text_to_speech(self, text, voice, speed, pitch, volume, output_file):
        async def _synth():
            comm = edge_tts.Communicate(
                text, voice,
                rate=f"{(speed-1)*100:+.0f}%",
                pitch=f"{pitch:+.0f}Hz",
                volume=f"{volume:+.0f}%"
            )
            await comm.save(output_file)
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
        self.root.geometry("800x600")
        self.stop_flag = False
        self.create_ui()

    def create_ui(self):
        main = ttk.Frame(self.root, padding="15")
        main.grid(row=0, column=0, sticky="nsew")
        self.root.columnconfigure(0, weight=1)
        self.root.rowconfigure(0, weight=1)
        
        # 为蓝色按钮创建样式
        style = ttk.Style(self.root)
        style.configure("Accent.TButton", foreground="white", background="#0078D7", padding=6)
        
        # ===== 设置区域 (无外框) =====
        settings_frame = ttk.Frame(main, padding="10")
        settings_frame.grid(row=0, column=0, sticky="ew")
        settings_frame.columnconfigure(0, weight=1)

        # 发音人 + 试听
        voice_frame = ttk.Frame(settings_frame)
        voice_frame.pack(fill="x", pady=5)
        ttk.Label(voice_frame, text="发音人:").pack(side="left", padx=(0, 10))
        self.voice_var = tk.StringVar(value=self.config["edge"]["voice_name"])
        self.voice_combo = ttk.Combobox(voice_frame, textvariable=self.voice_var, values=self.edge.voices, width=35)
        self.voice_combo.pack(side="left", padx=5, expand=True, fill="x")
        self.preview_button = ttk.Button(voice_frame, text="试听", command=self.preview_audio, width=10)
        self.preview_button.pack(side="left", padx=(10, 0))

        # 语速/音调/音量 一行
        controls_frame = ttk.Frame(settings_frame)
        controls_frame.pack(fill="x", pady=10)
        controls_frame.columnconfigure(0, weight=1)
        controls_frame.columnconfigure(1, weight=1)
        controls_frame.columnconfigure(2, weight=1)

        def make_slider(parent, label_text, var, from_, to, fmt):
            frame = ttk.Frame(parent)
            
            label_var = tk.StringVar(value=f"{label_text}: {fmt.format(var.get())}")
            label = ttk.Label(frame, textvariable=label_var, width=12, anchor="center")
            label.pack(fill="x")
            
            scale = ttk.Scale(frame, from_=from_, to=to, variable=var, orient="horizontal")
            scale.pack(fill="x", padx=5)
            
            var.trace_add("write", lambda *args: label_var.set(f"{label_text}: {fmt.format(var.get())}"))
            return frame

        self.speed_var = tk.DoubleVar(value=self.config["edge"]["speed"])
        make_slider(controls_frame, "语速", self.speed_var, 0.5, 2.0, "{:.2f}").grid(row=0, column=0, sticky="ew")
        self.pitch_var = tk.DoubleVar(value=self.config["edge"]["pitch"])
        make_slider(controls_frame, "音调", self.pitch_var, -50, 50, "{:+.0f}Hz").grid(row=0, column=1, sticky="ew")
        self.volume_var = tk.DoubleVar(value=self.config["edge"]["volume"])
        make_slider(controls_frame, "音量", self.volume_var, -100, 100, "{:+.0f}%").grid(row=0, column=2, sticky="ew")

        # TXT 目录
        dir_frame = ttk.Frame(settings_frame)
        dir_frame.pack(fill="x", pady=(10, 5))
        ttk.Label(dir_frame, text="TXT 目录:").pack(side="left", padx=(0, 10))
        self.txt_dir = tk.StringVar(value=self.config.get("last_txt_dir", ""))
        dir_entry = ttk.Entry(dir_frame, textvariable=self.txt_dir)
        dir_entry.pack(side="left", expand=True, fill="x", padx=5)
        ttk.Button(dir_frame, text="浏览...", command=self.select_input_dir).pack(side="left")

        # ===== 操作按钮 =====
        button_frame = ttk.Frame(main)
        button_frame.grid(row=2, column=0, pady=20, sticky="ew")
        # 使用空的列来创建间隔，实现居中和靠右的效果
        button_frame.columnconfigure(0, weight=1)
        button_frame.columnconfigure(1, weight=1)
        button_frame.columnconfigure(2, weight=1)
        button_frame.columnconfigure(3, weight=1)

        ttk.Button(button_frame, text="停止", command=self.stop_generation, width=15).grid(row=0, column=0, sticky="e")
        ttk.Button(button_frame, text="打开输出目录", command=self.open_output_dir, width=15).grid(row=0, column=1, padx=10, sticky="w")
        ttk.Button(button_frame, text="开始生成", command=self.start_generation, style="Accent.TButton", width=15).grid(row=0, column=3, sticky="e")

        # ===== 进度与日志 =====
        progress_frame = ttk.Frame(main)
        progress_frame.grid(row=3, column=0, sticky="ew")
        progress_frame.columnconfigure(0, weight=1)
        self.progress_var = tk.DoubleVar(value=0)
        self.progress_bar = ttk.Progressbar(progress_frame, variable=self.progress_var, maximum=100)
        self.progress_bar.grid(row=0, column=0, sticky="ew")

        # 日志区域 (无外框)
        log_frame = ttk.Frame(main)
        log_frame.grid(row=4, column=0, sticky="nsew", pady=(10, 0))
        log_frame.columnconfigure(0, weight=1)
        log_frame.rowconfigure(0, weight=1)
        main.rowconfigure(4, weight=1)

        self.log = tk.Text(log_frame, height=10, wrap="word", relief="solid", borderwidth=1)
        scrollbar = ttk.Scrollbar(log_frame, orient="vertical", command=self.log.yview)
        self.log.configure(yscrollcommand=scrollbar.set)
        self.log.grid(row=0, column=0, sticky="nsew")
        scrollbar.grid(row=0, column=1, sticky="ns")

        self.root.protocol("WM_DELETE_WINDOW", self.on_closing)

    def log_message(self, msg):
        self.root.after(0, lambda: self.log.insert("end", f"[{time.strftime('%H:%M:%S')}] {msg}\n"))
        self.root.after(0, lambda: self.log.see("end"))

    def select_input_dir(self):
        last_dir = self.config.get("last_txt_dir")
        initial_dir = os.path.dirname(last_dir) if last_dir and os.path.isdir(last_dir) else os.path.expanduser("~")
        p = filedialog.askdirectory(initialdir=initial_dir)
        if p:
            self.txt_dir.set(p)

    def preview_audio(self):
        def worker():
            text = "你好，我是你的有声书助手，这是试听效果。"
            tmp_file = None
            try:
                with tempfile.NamedTemporaryFile(delete=False, suffix=".mp3") as tmp:
                    tmp_file = tmp.name
                
                self.edge.text_to_speech(
                    text=text, voice=self.voice_var.get(),
                    speed=self.speed_var.get(), pitch=self.pitch_var.get(),
                    volume=self.volume_var.get(), output_file=tmp_file
                )
                
                self.log_message("试听文件生成成功，开始播放...")
                audio = AudioSegment.from_mp3(tmp_file)
                play_obj = sa.play_buffer(
                    audio.raw_data, audio.channels,
                    audio.sample_width, audio.frame_rate
                )
                play_obj.wait_done()
                self.log_message("试听播放完成。")
                
            except Exception as e:
                self.log_message(f"试听失败: {e}")
            finally:
                if tmp_file and os.path.exists(tmp_file): os.remove(tmp_file)
        
        threading.Thread(target=worker, daemon=True).start()

    def start_generation(self):
        self.stop_flag = False
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
        self.config["edge"]["voice_name"] = self.voice_var.get()
        self.config["edge"]["speed"] = self.speed_var.get()
        self.config["edge"]["pitch"] = self.pitch_var.get()
        self.config["edge"]["volume"] = self.volume_var.get()
        self.config["last_txt_dir"] = self.txt_dir.get()
        save_config(self.config)
        self.root.destroy()

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

    def run(self):
        self.root.mainloop()

if __name__ == "__main__":
    AudiobookGenerator().run()