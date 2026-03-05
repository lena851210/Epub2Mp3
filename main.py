# -*- coding: utf-8 -*-
"""
模块4：主应用程序 - GUI 界面和核心业务逻辑
"""

import os
import re
import tkinter as tk
from tkinter import filedialog, messagebox, ttk
import subprocess
import threading
import time
import tempfile
import platform
import shutil
from typing import List, Tuple, Optional
from concurrent.futures import ThreadPoolExecutor

from pydub import AudioSegment

from models import (
    ConfigManager,
    DurationEstimator,
    EdgeTTSWrapper,
    VOICE_MAPPING,
    BASE_WORDS_PER_MINUTE,
    sanitize_filename
)
from epub_processor import convert_epub_to_txt
from audio_processor import _process_audio_chunk, preprocess_text


# ====== 【Class】主应用类 ======
class AudiobookGenerator:
    """有声书生成工具 - 主应用类"""
    
    def __init__(self):
        self.config_mgr = ConfigManager(self._get_config_path())
        self.edge = EdgeTTSWrapper()
        self.duration_estimator = DurationEstimator(BASE_WORDS_PER_MINUTE)
        
        self.root = tk.Tk()
        self.root.title("有声书生成工具 (Edge TTS) - 优化版 v3.0")
        self.root.geometry("960x680")
        self.root.minsize(720, 560)
        self.stop_flag = False

        # UI 状态管理
        self.selection_states = {}
        self.selection_vars = {}
        self.tree_checks = {}
        self.progress_vars = {}
        self.tree_progress = {}
        self.file_chars = {}
        self.error_detail = {}
        
        # 动画效果
        self.spinner_frames = ["⠋","⠙","⠹","⠸","⠼","⠴","⠦","⠧","⠇","⠏"]
        self.spinner_active = {}
        self.spinner_job = None
        self._resize_job = None
        
        # 线程池
        self.tts_executor = ThreadPoolExecutor(max_workers=2)

        self.create_ui()

    def _get_config_path(self) -> str:
        """获取配置文件路径"""
        return os.path.join(os.path.dirname(os.path.abspath(__file__)), "config.json")

    def create_ui(self):
        """创建 UI 界面"""
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
        main.rowconfigure(2, weight=1)
        main.columnconfigure(0, weight=1)

        # ===== 语音设置 =====
        voice_lf = ttk.LabelFrame(main, text="语音设置", padding=(10, 8))
        voice_lf.grid(row=0, column=0, sticky="ew", padx=0, pady=(0, 8))
        voice_lf.columnconfigure(1, weight=1)

        ttk.Label(voice_lf, text="音色:").grid(row=0, column=0, padx=(0, 8), pady=(0, 6), sticky="w")
        current_voice = next((d for d, e in VOICE_MAPPING.items() if e == self.config_mgr.get("edge", {}).get("voice_name")), "晓晓(女)")
        self.voice_var = tk.StringVar(value=current_voice)
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

        self.speed_var = tk.DoubleVar(value=self.config_mgr.get("edge", {}).get("speed", 1.0))
        speed_frame = make_slider(sliders, "语速", self.speed_var, 0.5, 2.0, "{:.2f}x")
        speed_frame.grid(row=0, column=0, sticky="ew", padx=(0, 10))

        self.pitch_var = tk.DoubleVar(value=self.config_mgr.get("edge", {}).get("pitch", 0))
        make_slider(sliders, "音调", self.pitch_var, -50, 50, "{:+.0f}Hz").grid(row=0, column=1, sticky="ew", padx=(0, 10))
        self.volume_var = tk.DoubleVar(value=self.config_mgr.get("edge", {}).get("volume", 0))
        make_slider(sliders, "音量", self.volume_var, -100, 100, "{:+.0f}%").grid(row=0, column=2, sticky="ew")

        def update_wpm(*args):
            estimated_wpm = max(1, int(BASE_WORDS_PER_MINUTE * self.speed_var.get()))
            self.wpm_var.set(estimated_wpm)
            self.wpm_label_var.set(f"估算字数: {estimated_wpm} 字/分钟")
            self.update_all_estimates()
        
        self.wpm_var = tk.IntVar(value=self.config_mgr.get("words_per_minute", BASE_WORDS_PER_MINUTE))
        self.wpm_label_var = tk.StringVar(value=f"估算字数: {self.wpm_var.get()} 字/分钟")
        self.speed_var.trace_add("write", update_wpm)

        # ===== 输出设置 =====
        out_lf = ttk.LabelFrame(main, text="输出设置", padding=(10, 8))
        out_lf.grid(row=1, column=0, sticky="ew", padx=0, pady=(0, 8))
        out_lf.columnconfigure(1, weight=1)

        self.merge_var = tk.BooleanVar(value=self.config_mgr.get("merge_audio", True))
        merge_row = ttk.Frame(out_lf)
        merge_row.grid(row=0, column=0, columnspan=4, sticky="ew", pady=(0, 6))
        ttk.Checkbutton(merge_row, text="合并音频为长段落", variable=self.merge_var, command=self.toggle_merge_options).pack(side="left")
        ttk.Label(merge_row, text="目标时长(分钟):").pack(side="left", padx=(10, 6))
        self.target_duration_var = tk.IntVar(value=self.config_mgr.get("target_duration", 40))
        self.target_duration_spin = ttk.Spinbox(merge_row, from_=10, to=120, width=6, textvariable=self.target_duration_var)
        self.target_duration_spin.pack(side="left", padx=(0, 12))
        ttk.Label(merge_row, textvariable=self.wpm_label_var).pack(side="left")

        dir_row = ttk.Frame(out_lf)
        dir_row.grid(row=1, column=0, columnspan=4, sticky="ew")
        dir_row.columnconfigure(1, weight=1)
        ttk.Label(dir_row, text="TXT目录:").grid(row=0, column=0, padx=(0, 8), sticky="w")
        self.txt_dir = tk.StringVar(value=self.config_mgr.get("last_txt_dir", ""))
        ttk.Entry(dir_row, textvariable=self.txt_dir).grid(row=0, column=1, sticky="ew", padx=(0, 8))
        ttk.Button(dir_row, text="浏览...", command=self.select_input_dir, width=8).grid(row=0, column=2, sticky="e")

        # ===== 源文件列表 =====
        files_lf = ttk.LabelFrame(main, text="源文件", padding=(10, 8))
        files_lf.grid(row=2, column=0, sticky="nsew", padx=0, pady=(0, 8))
        files_lf.columnconfigure(0, weight=1)
        files_lf.rowconfigure(2, weight=1)

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

        # 表格配置
        columns = ("select", "name", "size", "chars", "est", "status", "progress")
        self.files_tree = ttk.Treeview(files_lf, columns=columns, show="headings")
        self.files_tree.heading("select", text="选择")
        self.files_tree.heading("name", text="TXT名称")
        self.files_tree.heading("size", text="大小(KB)")
        self.files_tree.heading("chars", text="字数")
        self.files_tree.heading("est", text="预估时长")
        self.files_tree.heading("status", text="状态")
        self.files_tree.heading("progress", text="进度")

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

        self.files_tree.bind("<Configure>", lambda e: self._on_tree_configure())
        self.files_tree.bind("<Double-1>", self.on_tree_double_click)
        self.files_tree.bind("<Button-1>", self._on_tree_click)

        # 底部按钮
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

        self.root.protocol("WM_DELETE_WINDOW", self.on_closing)
        if self.txt_dir.get() and os.path.isdir(self.txt_dir.get()):
            self.load_file_list(self.txt_dir.get())
        self._warn_if_no_ffmpeg()
        self.update_all_estimates()

    # ====== 工具方法 ======

    def _has_ffmpeg(self) -> bool:
        """检查是否安装了 ffmpeg"""
        return shutil.which("ffmpeg") is not None

    def _warn_if_no_ffmpeg(self):
        """如果没有 ffmpeg 则警告"""
        if not self._has_ffmpeg():
            self.set_status("未检测到 ffmpeg，合并/导出可能失败。macOS 可执行: brew install ffmpeg")

    def set_status(self, text: str):
        """设置状态栏文本"""
        self.root.after(0, lambda: self.status_var.set(text))

    def _on_tree_configure(self):
        """表格配置改变时刷新"""
        self.refresh_tree_overlays()

    def seconds_to_str(self, total_seconds: int) -> str:
        """秒数转字符串"""
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
        """估算时长字符串"""
        wpm = max(1, self.wpm_var.get())
        seconds = self.duration_estimator.estimate_seconds(chars, wpm)
        return self.seconds_to_str(seconds)

    def update_all_estimates(self):
        """更新所有文件的预估时长"""
        try:
            for iid in self.files_tree.get_children():
                chars = self.file_chars.get(iid, None)
                if chars is None:
                    try:
                        chars = int(self.files_tree.set(iid, "chars"))
                        self.file_chars[iid] = chars
                    except Exception:
                        continue
                self.files_tree.set(iid, "est", self.estimate_duration_str(chars))
        except Exception:
            pass

    def get_mp3_duration_str(self, path: str) -> Optional[str]:
        """获取 MP3 文件时长"""
        try:
            audio = AudioSegment.from_file(path, format="mp3")
            ms = len(audio)
            return self.seconds_to_str(int(round(ms / 1000)))
        except Exception:
            return None

    def refresh_voices(self):
        """刷新语音列表"""
        def task():
            self.set_status("正在刷新音色列表...")
            voices = self.edge.refresh_voices()
            self.root.after(0, lambda: self.voice_combo.configure(values=voices))
            self.set_status(f"音色列表已刷新，共 {len(voices)} 个")
        threading.Thread(target=task, daemon=True).start()

    def toggle_merge_options(self):
        """切换合并选项的启用状态"""
        state = "normal" if self.merge_var.get() else "disabled"
        self.target_duration_spin.configure(state=state)

    def select_input_dir(self):
        """选择输入目录"""
        last_dir = self.txt_dir.get()
        initial_dir = last_dir if last_dir and os.path.isdir(last_dir) else os.path.expanduser("~")
        p = filedialog.askdirectory(initialdir=initial_dir)
        if p:
            self.txt_dir.set(p)
            self.config_mgr.set("last_txt_dir", p)
            self.set_status("已选择目录")
            self.load_file_list(p)

    def read_text_file(self, path: str) -> Optional[str]:
        """读取文本文件"""
        try:
            with open(path, "r", encoding="utf-8", errors="ignore") as f:
                return f.read()
        except Exception:
            return None

    # ====== 文件列表管理 ======

    def load_file_list(self, directory: str):
        """加载 TXT 文件列表"""
        for iid in self.files_tree.get_children():
            self.files_tree.delete(iid)
        
        self.selection_states.clear()
        self.selection_vars.clear()
        self.tree_checks.clear()
        self.file_chars.clear()

        if not os.path.isdir(directory):
            self.files_info_var.set("当前目录无效")
            return

        files = [f for f in sorted(os.listdir(directory)) if f.lower().endswith(".txt")]
        if not files:
            self.files_info_var.set("该目录下没有 TXT 文件")
            return

        for idx, fname in enumerate(files):
            full_path = os.path.join(directory, fname)
            
            try:
                size_kb = os.path.getsize(full_path) / 1024.0
                size_str = f"{size_kb:.1f}"
            except Exception:
                size_str = "-"

            text = self.read_text_file(full_path)
            if text is None:
                chars = 0
            else:
                chars = self.duration_estimator.count_chars(text)

            self.file_chars[fname] = chars
            est_str = self.estimate_duration_str(chars)

            iid = fname
            self.selection_states[iid] = True
            self.progress_vars[iid] = tk.DoubleVar(value=0.0)
            
            # 第一列显示 ✓ 或空白
            check_mark = "✓" if self.selection_states[iid] else ""
            
            tag = "oddrow" if idx % 2 else "evenrow"
            self.files_tree.insert(
                "", "end", iid=iid,
                values=(check_mark, fname, size_str, str(chars), est_str, "待处理", ""),
                tags=(tag,)
            )

        self.update_selection_info()

    def _on_tree_click(self, event):
        """点击表格行来切换选择状态"""
        row = self.files_tree.identify_row(event.y)
        col = self.files_tree.identify_column(event.x)
        
        if not row or not col:
            return
        
        # 如果点击的是第一列或第二列，切换选中状态
        if col in ("#1", "#2"):
            self._toggle_selection(row)

    def _toggle_selection(self, iid: str):
        """切换单个文件的选择状态"""
        self.selection_states[iid] = not self.selection_states.get(iid, False)
        
        # 更新表格显示
        check_mark = "✓" if self.selection_states[iid] else ""
        current_values = self.files_tree.item(iid, "values")
        new_values = (check_mark,) + current_values[1:]
        self.files_tree.item(iid, values=new_values)
        
        # 更新统计信息
        self.update_selection_info()

    def refresh_tree_overlays(self):
        """刷新进度条位置"""
        tree_h = self.files_tree.winfo_height()

        for iid in self.files_tree.get_children(""):
            bbox_prog = self.files_tree.bbox(iid, column="#7")
            if bbox_prog:
                x, y, w, h = bbox_prog
                if not (y + h < 0 or y > tree_h):
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
                    if pb:
                        pb.place_forget()

    def update_selection_info(self):
        """更新选择信息"""
        total = len(self.selection_states)
        selected = sum(1 for v in self.selection_states.values() if v)
        self.files_info_var.set(f"已选择 {selected}/{total} 个文件")

    def select_all_files(self):
        """全选所有文件"""
        for iid in self.files_tree.get_children():
            self.selection_states[iid] = True
            current_values = self.files_tree.item(iid, "values")
            new_values = ("✓",) + current_values[1:]
            self.files_tree.item(iid, values=new_values)
        self.update_selection_info()

    def unselect_all_files(self):
        """全不选"""
        for iid in self.files_tree.get_children():
            self.selection_states[iid] = False
            current_values = self.files_tree.item(iid, "values")
            new_values = ("",) + current_values[1:]
            self.files_tree.item(iid, values=new_values)
        self.update_selection_info()

    def invert_selection(self):
        """反选"""
        for iid in self.files_tree.get_children():
            cur = self.selection_states.get(iid, True)
            self.selection_states[iid] = not cur
            check_mark = "✓" if self.selection_states[iid] else ""
            current_values = self.files_tree.item(iid, "values")
            new_values = (check_mark,) + current_values[1:]
            self.files_tree.item(iid, values=new_values)
        self.update_selection_info()

    def on_tree_double_click(self, event):
        """双击打开文件预览"""
        region = self.files_tree.identify_region(event.x, event.y)
        if region != "cell":
            return
        col = self.files_tree.identify_column(event.x)
        row = self.files_tree.identify_row(event.y)
        if not row:
            return
        if col == "#2":
            directory = self.txt_dir.get()
            path = os.path.join(directory, row)
            self.open_text_preview(path)

    def open_text_preview(self, path: str):
        """打开文本预览"""
        try:
            sysname = platform.system()
            if sysname == "Darwin":
                subprocess.run(["open", path], check=False)
            elif sysname == "Windows":
                os.startfile(path)
            else:
                subprocess.run(["xdg-open", path], check=False)
            self.set_status(f"已打开预览: {os.path.basename(path)}")
        except Exception as e:
            self.set_status(f"预览失败: {e}")

    def set_file_status(self, iid: str, status_text: str, spinning: bool = False):
        """设置文件状态"""
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
        """动画刻度"""
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

    def show_error_detail(self, iid: str):
        """显示错误详情"""
        msg = self.error_detail.get(iid, "")
        if not msg:
            messagebox.showinfo("详情", "无错误详情。")
        else:
            messagebox.showerror("失败原因", msg)

    def preview_audio(self):
        """试听音频"""
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
                err_msg = str(e)
                self.root.after(0, lambda msg=err_msg: messagebox.showerror("试听失败", f"无法连接到语音服务。\n\n错误信息：\n{msg}"))
            finally:
                if tmp_file and os.path.exists(tmp_file):
                    try:
                        os.remove(tmp_file)
                    except Exception:
                        pass
        threading.Thread(target=worker, daemon=True).start()

    def start_generation(self):
        """开始转换任务"""
        self.stop_flag = False
        if not self._has_ffmpeg():
            self.set_status("未检测到 ffmpeg，若分段>1将无法合并；��尽量退化处理。")
        self.set_status("开始转换任务...")
        threading.Thread(target=self.generate, daemon=True).start()

    def stop_generation(self):
        """停止转换任务"""
        self.stop_flag = True
        self.set_status("用户请求停止，正在中止任务...")

    def open_output_dir(self):
        """打开输出目录"""
        txt_dir = self.txt_dir.get()
        if not txt_dir or not os.path.isdir(txt_dir):
            messagebox.showerror("错误", "请先选择有效的TXT目录")
            return
        out_dir = os.path.join(txt_dir, "Audio")
        os.makedirs(out_dir, exist_ok=True)
        if platform.system() == "Darwin": 
            os.system(f'open "{out_dir}"')
        elif platform.system() == "Windows": 
            os.startfile(out_dir)
        else: 
            os.system(f'xdg-open "{out_dir}"')

    # ====== 音频生成逻辑 ======

    def estimate_duration(self, text: str) -> float:
        """估算���长（分钟）"""
        wpm = max(1, self.wpm_var.get())
        clean_text = text.replace(" ", "").replace("\n", "").replace("\t", "")
        char_count = len(clean_text)
        return char_count / wpm

    def split_long_text(self, text: str, target_duration: int, file_name: str) -> List[Tuple[str, List[str]]]:
        """将长文本分割为多个接近目标时长的部分"""
        wpm = max(1, self.wpm_var.get())
        target_chars = target_duration * wpm  # 目标字数
        
        parts = []
        paragraphs = text.split("\n\n")
        
        current = ""
        for para in paragraphs:
            if not para.strip():
                continue
            
            test = current + "\n\n" + para if current else para
            clean_len = len(test.replace(" ", "").replace("\n", "").replace("\t", ""))
            
            if clean_len <= target_chars:
                current = test
            else:
                if current:
                    parts.append((current, [file_name]))
                
                para_clean_len = len(para.replace(" ", "").replace("\n", "").replace("\t", ""))
                
                if para_clean_len > target_chars:
                    sub_parts = self._split_paragraph(para, target_chars)
                    parts.extend([(p, [file_name]) for p in sub_parts])
                    current = ""
                else:
                    current = para
        
        if current:
            parts.append((current, [file_name]))
        
        return parts if parts else [(text, [file_name])]

    def _split_paragraph(self, paragraph: str, target_chars: int) -> List[str]:
        """递归分割超长段落"""
        para_clean_len = len(paragraph.replace(" ", "").replace("\n", "").replace("\t", ""))
        
        if para_clean_len <= target_chars:
            return [paragraph]
        
        sentences = paragraph.replace("！", "！\n").replace("？", "？\n").replace("。", "。\n").split("\n")
        
        parts = []
        current = ""
        
        for sentence in sentences:
            sentence = sentence.strip()
            if not sentence:
                continue
            
            test = current + sentence if not current else current + "\n" + sentence
            test_len = len(test.replace(" ", "").replace("\n", "").replace("\t", ""))
            
            if test_len <= target_chars:
                current = test
            else:
                if current:
                    parts.append(current)
                
                sentence_len = len(sentence.replace(" ", "").replace("\n", "").replace("\t", ""))
                if sentence_len > target_chars:
                    sub_parts = self._split_by_chars(sentence, target_chars)
                    parts.extend(sub_parts)
                    current = ""
                else:
                    current = sentence
        
        if current:
            parts.append(current)
        
        return parts

    def _split_by_chars(self, text: str, target_chars: int) -> List[str]:
        """按字符数强制分割"""
        if len(text) <= target_chars:
            return [text]
        
        parts = []
        for i in range(0, len(text), target_chars):
            parts.append(text[i:i+target_chars])
        
        return parts

    def get_selected_files(self) -> List[str]:
        """获取已选择的文件列表"""
        directory = self.txt_dir.get()
        if not directory or not os.path.isdir(directory):
            return []
        return [iid for iid in self.files_tree.get_children() if self.selection_states.get(iid, False)]

    def set_file_progress(self, iid: str, percent: float):
        """设置文件进度"""
        var = self.progress_vars.get(iid)
        if var is None:
            var = tk.DoubleVar(value=0.0)
            self.progress_vars[iid] = var
        self.root.after(0, lambda v=var, p=percent: v.set(max(0.0, min(100.0, float(p)))))

    def set_error(self, iid: str, exc: Exception):
        """设置错误信息"""
        self.error_detail[iid] = str(exc)

    def tts_with_retry(self, text: str, output_file: str, iid_for_error: Optional[str] = None, max_retries: int = 3) -> bool:
        """TTS 转换 - 支持重试"""
        last_exc = None
        parent = os.path.dirname(output_file) or "."
        os.makedirs(parent, exist_ok=True)

        for n in range(1, max_retries + 1):
            try:
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

                if os.path.exists(output_file) and os.path.getsize(output_file) > 0:
                    return True
                else:
                    raise RuntimeError(f"TTS输出为空或未生成: {os.path.basename(output_file)}")

            except Exception as e:
                last_exc = e
                if n < max_retries:
                    time.sleep(min(1.5, 0.5 * n))

        if iid_for_error:
            self.set_error(iid_for_error, last_exc)
        return False

    def generate(self):
        """生成有声书"""
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

    def generate_single_files(self, files: List[str], txt_dir: str, out_dir: str):
        """单文件转换模式 - 支持长文本分割"""
        part_num = 1
        for i, f in enumerate(files, 1):
            if self.stop_flag:
                break
            
            ipath = os.path.join(txt_dir, f)
            text = self.read_text_file(ipath)
            if text is None:
                self.set_file_status(f, "失败：无法读取文件", spinning=False)
                self.set_error(f, f"无法读取文件: {ipath}")
                continue
            
            text = text.strip()
            file_duration = self.estimate_duration(text)
            
            # 检查是否需要分割
            if file_duration > 60:  # 超过 60 分钟就分割成 40 分钟的部分
                sub_parts = self.split_long_text(text, 40, f)
                for idx, (sub_text, sub_files) in enumerate(sub_parts, 1):
                    if self.stop_flag:
                        break
                    _process_audio_chunk(
                        text=sub_text,
                        out_dir=out_dir,
                        part_num=part_num,
                        file_list=sub_files,
                        edge_tts_wrapper=self.edge,
                        voice_var=self.voice_var,
                        speed_var=self.speed_var,
                        pitch_var=self.pitch_var,
                        volume_var=self.volume_var,
                        set_file_status=self.set_file_status,
                        set_file_progress=self.set_file_progress,
                        set_error=self.set_error,
                        get_mp3_duration_str=self.get_mp3_duration_str,
                        seconds_to_str=self.seconds_to_str,
                        stop_flag_check=lambda: self.stop_flag,
                        tts_with_retry=self.tts_with_retry
                    )
                    part_num += 1
            else:
                _process_audio_chunk(
                    text=text,
                    out_dir=out_dir,
                    part_num=part_num,
                    file_list=[f],
                    edge_tts_wrapper=self.edge,
                    voice_var=self.voice_var,
                    speed_var=self.speed_var,
                    pitch_var=self.pitch_var,
                    volume_var=self.volume_var,
                    set_file_status=self.set_file_status,
                    set_file_progress=self.set_file_progress,
                    set_error=self.set_error,
                    get_mp3_duration_str=self.get_mp3_duration_str,
                    seconds_to_str=self.seconds_to_str,
                    stop_flag_check=lambda: self.stop_flag,
                    tts_with_retry=self.tts_with_retry
                )
                part_num += 1

    def generate_merged_files(self, files: List[str], txt_dir: str, out_dir: str):
        """合并模式 - 支持长文本分割"""
        target_duration = self.target_duration_var.get()
        part_num = 1
        current_text = ""
        current_duration = 0.0
        current_files = []

        for i, f in enumerate(files, 1):
            if self.stop_flag:
                break

            ipath = os.path.join(txt_dir, f)
            text = self.read_text_file(ipath)
            if text is None:
                self.set_file_status(f, "失败：无法读取文件", spinning=False)
                self.set_error(f, f"无法读取文件: {ipath}")
                continue
            
            text = text.strip()
            file_duration = self.estimate_duration(text)

            # 单个文件超过目标��长
            if file_duration >= target_duration:
                # 先处理累积的文本
                if current_text and not self.stop_flag:
                    _process_audio_chunk(
                        text=current_text,
                        out_dir=out_dir,
                        part_num=part_num,
                        file_list=current_files,
                        edge_tts_wrapper=self.edge,
                        voice_var=self.voice_var,
                        speed_var=self.speed_var,
                        pitch_var=self.pitch_var,
                        volume_var=self.volume_var,
                        set_file_status=self.set_file_status,
                        set_file_progress=self.set_file_progress,
                        set_error=self.set_error,
                        get_mp3_duration_str=self.get_mp3_duration_str,
                        seconds_to_str=self.seconds_to_str,
                        stop_flag_check=lambda: self.stop_flag,
                        tts_with_retry=self.tts_with_retry
                    )
                    part_num += 1
                    current_text = ""
                    current_duration = 0.0
                    current_files = []

                # 将长文本分割为多个部分
                sub_parts = self.split_long_text(text, target_duration, f)
                for sub_text, sub_files in sub_parts:
                    if self.stop_flag:
                        break
                    _process_audio_chunk(
                        text=sub_text,
                        out_dir=out_dir,
                        part_num=part_num,
                        file_list=sub_files,
                        edge_tts_wrapper=self.edge,
                        voice_var=self.voice_var,
                        speed_var=self.speed_var,
                        pitch_var=self.pitch_var,
                        volume_var=self.volume_var,
                        set_file_status=self.set_file_status,
                        set_file_progress=self.set_file_progress,
                        set_error=self.set_error,
                        get_mp3_duration_str=self.get_mp3_duration_str,
                        seconds_to_str=self.seconds_to_str,
                        stop_flag_check=lambda: self.stop_flag,
                        tts_with_retry=self.tts_with_retry
                    )
                    part_num += 1
                continue

            # 累积小文件
            self.set_file_status(f, "等待合并", spinning=False)
            
            if current_duration + file_duration <= target_duration:
                # 还没到目标时长，继续累积
                current_text += ("\n\n" + text) if current_text else text
                current_duration += file_duration
                current_files.append(f)
            else:
                # 加上这个文件会超过目标时长，先处理累积的
                if current_text and not self.stop_flag:
                    _process_audio_chunk(
                        text=current_text,
                        out_dir=out_dir,
                        part_num=part_num,
                        file_list=current_files,
                        edge_tts_wrapper=self.edge,
                        voice_var=self.voice_var,
                        speed_var=self.speed_var,
                        pitch_var=self.pitch_var,
                        volume_var=self.volume_var,
                        set_file_status=self.set_file_status,
                        set_file_progress=self.set_file_progress,
                        set_error=self.set_error,
                        get_mp3_duration_str=self.get_mp3_duration_str,
                        seconds_to_str=self.seconds_to_str,
                        stop_flag_check=lambda: self.stop_flag,
                        tts_with_retry=self.tts_with_retry
                    )
                    part_num += 1
                
                # 新一轮，从当前文件开始
                current_text = text
                current_duration = file_duration
                current_files = [f]

        # 处理最后剩余的文本
        if current_text and not self.stop_flag:
            _process_audio_chunk(
                text=current_text,
                out_dir=out_dir,
                part_num=part_num,
                file_list=current_files,
                edge_tts_wrapper=self.edge,
                voice_var=self.voice_var,
                speed_var=self.speed_var,
                pitch_var=self.pitch_var,
                volume_var=self.volume_var,
                set_file_status=self.set_file_status,
                set_file_progress=self.set_file_progress,
                set_error=self.set_error,
                get_mp3_duration_str=self.get_mp3_duration_str,
                seconds_to_str=self.seconds_to_str,
                stop_flag_check=lambda: self.stop_flag,
                tts_with_retry=self.tts_with_retry
            )

    def import_epub(self):
        """导入 EPUB 文件"""
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
            self.txt_dir.set(out_dir)
            self.config_mgr.set("last_txt_dir", out_dir)
            self.load_file_list(out_dir)
            self.set_status(f"EPUB转换完成：生成 {converted_count} 章 / {total_files} 个TXT")
        except Exception as e:
            messagebox.showerror("EPUB转换失败", str(e))
            self.set_status("EPUB转换失败")

    def on_closing(self):
        """窗口关闭时保存配置"""
        self.stop_flag = True
        edge_voice_name = VOICE_MAPPING.get(self.voice_var.get(), "zh-CN-XiaoxiaoNeural")
        self.config_mgr.set("edge", {
            "voice_name": edge_voice_name,
            "speed": self.speed_var.get(),
            "pitch": self.pitch_var.get(),
            "volume": self.volume_var.get()
        })
        self.config_mgr.set("last_txt_dir", self.txt_dir.get())
        self.config_mgr.set("merge_audio", self.merge_var.get())
        self.config_mgr.set("target_duration", self.target_duration_var.get())
        self.config_mgr.set("words_per_minute", self.wpm_var.get())
        self.config_mgr.flush()
        self.root.destroy()

    def run(self):
        """运行应用"""
        self.root.mainloop()          