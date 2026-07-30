# -*- coding: utf-8 -*-
"""
模块4：主应用程序 - GUI 界面和核心业务逻辑
"""

import os
import tkinter as tk
from tkinter import filedialog, messagebox, ttk
import subprocess
import tempfile
import platform
import shutil
from typing import Optional
from concurrent.futures import ThreadPoolExecutor

from pydub import AudioSegment

from models import (
    ConfigManager,
    DurationEstimator,
    EdgeTTSWrapper,
    VOICE_MAPPING,
    BASE_WORDS_PER_MINUTE,
)
from epub_processor import convert_epub_to_txt
from generation_manager import GenerationMixin
from file_manager import FileManagerMixin


class AudiobookGenerator(FileManagerMixin, GenerationMixin):
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
        self.progress_vars = {}
        self.tree_progress = {}
        self.file_chars = {}
        self.error_detail = {}

        # 动画效果
        self.spinner_frames = ["⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏"]
        self.spinner_active = {}
        self.spinner_job = None

        # 目录监测
        self.dir_watch_job = None
        self.last_dir_snapshot = None

        # 线程池（目前保留）
        self.tts_executor = ThreadPoolExecutor(max_workers=2)

        self.create_ui()
        self._start_dir_watch()

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

        base_font = ("Helvetica" if sysname == "Darwin" else "Segoe UI", 11)
        style.configure(".", font=base_font)
        style.configure("Treeview.Heading", font=(base_font[0], base_font[1], "bold"))
        style.configure("Treeview", rowheight=28)
        style.configure("Primary.TButton", font=(base_font[0], base_font[1], "bold"), padding=(12, 7))

        main = ttk.Frame(self.root, padding=(12, 10, 12, 10))
        main.grid(row=0, column=0, sticky="nsew")
        self.root.columnconfigure(0, weight=1)
        self.root.rowconfigure(0, weight=1)

        main.columnconfigure(0, weight=1)
        main.rowconfigure(0, weight=3)
        main.rowconfigure(1, weight=0)
        main.rowconfigure(2, weight=0)
        main.rowconfigure(3, weight=0)
        main.rowconfigure(4, weight=0)

        # =========================
        # Step 1：文本准备
        # =========================
        files_lf = ttk.LabelFrame(main, text="Step 1：文本准备（导入 EPUB / 查看 TXT）", padding=(10, 8))
        files_lf.grid(row=0, column=0, sticky="nsew", pady=(0, 8))
        files_lf.columnconfigure(0, weight=1)
        files_lf.rowconfigure(2, weight=1)

        topbar = ttk.Frame(files_lf)
        topbar.grid(row=0, column=0, sticky="ew", pady=(0, 6))
        topbar.columnconfigure(0, weight=0)
        topbar.columnconfigure(1, weight=1)
        topbar.columnconfigure(2, weight=0)

        left_info = ttk.Frame(topbar)
        left_info.grid(row=0, column=0, sticky="w")
        ttk.Label(left_info, text="TXT列表（双击名称可预览）").pack(side="left", padx=(0, 12))

        mid_actions = ttk.Frame(topbar)
        mid_actions.grid(row=0, column=1, sticky="w")

        ttk.Button(mid_actions, text="【全选】", command=self.select_all_files, style="Toolbutton", width=8).pack(side="left", padx=(0, 6))
        ttk.Button(mid_actions, text="【全不选】", command=self.unselect_all_files, style="Toolbutton", width=8).pack(side="left", padx=(0, 6))
        ttk.Button(mid_actions, text="【反选】", command=self.invert_selection, style="Toolbutton", width=8).pack(side="left", padx=(0, 10))

        self.files_info_var = tk.StringVar(value="当前目录未加载")
        ttk.Label(mid_actions, textvariable=self.files_info_var, foreground="#666").pack(side="left")

        right_main_action = ttk.Frame(topbar)
        right_main_action.grid(row=0, column=2, sticky="e")

        self.import_epub_btn = ttk.Button(
            right_main_action,
            text="📘 导入 EPUB→TXT",
            command=self.import_epub,
            width=20,
            style="Primary.TButton"
        )
        self.import_epub_btn.pack(side="right")

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

        # =========================
        # Step 2：语音设置
        # =========================
        voice_lf = ttk.LabelFrame(main, text="Step 2：语音设置", padding=(10, 8))
        voice_lf.grid(row=1, column=0, sticky="ew", pady=(0, 8))
        voice_lf.columnconfigure(1, weight=1)

        ttk.Label(voice_lf, text="音色:").grid(row=0, column=0, padx=(0, 8), pady=(0, 6), sticky="w")

        current_voice = next(
            (d for d, e in VOICE_MAPPING.items() if e == self.config_mgr.get("edge", {}).get("voice_name")),
            (self.edge.voices[0] if self.edge.voices else "晓晓(女)")
        )
        if self.edge.voices and current_voice not in self.edge.voices:
            current_voice = self.edge.voices[0]

        self.voice_var = tk.StringVar(value=current_voice)
        self.voice_combo = ttk.Combobox(
            voice_lf,
            textvariable=self.voice_var,
            values=self.edge.voices,
            state="readonly"
        )
        self.voice_combo.grid(row=0, column=1, sticky="ew", padx=(0, 8), pady=(0, 6))

        ttk.Button(voice_lf, text="刷新列表", command=self.refresh_voices, width=10).grid(row=0, column=2, padx=(0, 6), pady=(0, 6), sticky="e")
        ttk.Button(voice_lf, text="试听", command=self.preview_audio, width=8).grid(row=0, column=3, pady=(0, 6), sticky="e")

        sliders = ttk.Frame(voice_lf)
        sliders.grid(row=1, column=0, columnspan=4, sticky="ew", pady=(4, 0))
        for i in range(3):
            sliders.columnconfigure(i, weight=1)

        def make_slider(parent, label_text, var, from_, to, fmt, extra_text_func=None):
            frame = ttk.Frame(parent)

            def build_label():
                base = f"{label_text}: {fmt.format(var.get())}"
                if extra_text_func:
                    extra = extra_text_func()
                    if extra:
                        base += extra
                return base

            label_var = tk.StringVar(value=build_label())
            ttk.Label(frame, textvariable=label_var, anchor="w").pack(fill="x")
            scale = ttk.Scale(frame, from_=from_, to=to, variable=var, orient="horizontal")
            scale.pack(fill="x")

            def refresh_label(*args):
                label_var.set(build_label())

            var.trace_add("write", refresh_label)
            return frame

        def current_wpm_text():
            estimated_wpm = max(1, int(BASE_WORDS_PER_MINUTE * self.speed_var.get()))
            return f"（约 {estimated_wpm} 字/分钟）"

        self.speed_var = tk.DoubleVar(value=self.config_mgr.get("edge", {}).get("speed", 1.0))
        speed_frame = make_slider(
            sliders,
            "语速",
            self.speed_var,
            0.5,
            2.0,
            "{:.2f}x",
            extra_text_func=current_wpm_text
        )
        speed_frame.grid(row=0, column=0, sticky="ew", padx=(0, 10))

        self.pitch_var = tk.DoubleVar(value=self.config_mgr.get("edge", {}).get("pitch", 0))
        make_slider(sliders, "音调", self.pitch_var, -50, 50, "{:+.0f}Hz").grid(row=0, column=1, sticky="ew", padx=(0, 10))

        self.volume_var = tk.DoubleVar(value=self.config_mgr.get("edge", {}).get("volume", 0))
        make_slider(sliders, "音量", self.volume_var, -100, 100, "{:+.0f}%").grid(row=0, column=2, sticky="ew")

        def update_wpm(*args):
            estimated_wpm = max(1, int(BASE_WORDS_PER_MINUTE * self.speed_var.get()))
            self.wpm_var.set(estimated_wpm)
            self.update_all_estimates()

        self.wpm_var = tk.IntVar(value=self.config_mgr.get("words_per_minute", BASE_WORDS_PER_MINUTE))
        self.speed_var.trace_add("write", update_wpm)

        # =========================
        # Step 3：输出设置
        # =========================
        out_lf = ttk.LabelFrame(main, text="Step 3：输出设置", padding=(10, 8))
        out_lf.grid(row=2, column=0, sticky="ew", pady=(0, 8))
        out_lf.columnconfigure(1, weight=1)

        self.merge_var = tk.BooleanVar(value=self.config_mgr.get("merge_audio", False))

        merge_row = ttk.Frame(out_lf)
        merge_row.grid(row=0, column=0, columnspan=4, sticky="ew", pady=(0, 6))

        self.merge_check = ttk.Checkbutton(
            merge_row,
            text="按目标时长合并/拆分TXT",
            variable=self.merge_var,
            command=self.toggle_merge_options
        )
        self.merge_check.pack(side="left")

        self.target_row = ttk.Frame(merge_row)
        ttk.Label(self.target_row, text="目标时长(分钟):").pack(side="left", padx=(12, 6))

        self.target_duration_var = tk.IntVar(value=self.config_mgr.get("target_duration", 30))
        self.target_duration_spin = ttk.Spinbox(
            self.target_row,
            from_=10,
            to=120,
            width=6,
            textvariable=self.target_duration_var
        )
        self.target_duration_spin.pack(side="left")

        dir_row = ttk.Frame(out_lf)
        dir_row.grid(row=1, column=0, columnspan=4, sticky="ew")
        dir_row.columnconfigure(1, weight=1)

        ttk.Label(dir_row, text="TXT目录:").grid(row=0, column=0, padx=(0, 8), sticky="w")
        self.txt_dir = tk.StringVar(value=self.config_mgr.get("last_txt_dir", ""))
        ttk.Entry(dir_row, textvariable=self.txt_dir).grid(row=0, column=1, sticky="ew", padx=(0, 8))
        ttk.Button(dir_row, text="浏览...", command=self.select_input_dir, width=8).grid(row=0, column=2, sticky="e")

        # 初始化 Step 3 显示
        self.toggle_merge_options(initial=True)

        # =========================
        # 底部操作栏
        # =========================
        action_bar = ttk.Frame(main)
        action_bar.grid(row=3, column=0, sticky="ew", pady=(4, 8))
        action_bar.columnconfigure(0, weight=1)
        action_bar.columnconfigure(1, weight=1)

        left_btns = ttk.Frame(action_bar)
        left_btns.grid(row=0, column=0, sticky="w")
        ttk.Button(left_btns, text="📁 打开音频目录", command=self.open_output_dir, width=16).pack(side="left")

        right_btns = ttk.Frame(action_bar)
        right_btns.grid(row=0, column=1, sticky="e")
        ttk.Button(right_btns, text="停止", command=self.stop_generation, width=10).pack(side="left", padx=(0, 10))

        self.start_btn = ttk.Button(
            right_btns,
            text="🚀 开始转换",
            command=self.start_generation,
            width=18,
            style="Primary.TButton"
        )
        self.start_btn.pack(side="left")

        self.root.bind("<Return>", lambda e: self.start_generation() if str(self.start_btn.cget("state")) != "disabled" else None)

        status_bar = ttk.Frame(main)
        status_bar.grid(row=4, column=0, sticky="ew")
        self.status_var = tk.StringVar(value="就绪")
        ttk.Label(status_bar, textvariable=self.status_var, anchor="w").pack(fill="x")

        self.root.protocol("WM_DELETE_WINDOW", self.on_closing)

        if self.txt_dir.get() and os.path.isdir(self.txt_dir.get()):
            self.load_file_list(self.txt_dir.get())

        self._warn_if_no_ffmpeg()
        self.update_all_estimates()
        self.update_action_buttons_state()

    # ====== 工具方法 ======

    def _has_ffmpeg(self) -> bool:
        """检查是否安装了 ffmpeg"""
        return shutil.which("ffmpeg") is not None

    def _warn_if_no_ffmpeg(self):
        """如果没有 ffmpeg 则警告"""
        if not self._has_ffmpeg():
            self.set_status("未检测到 ffmpeg，合并/导出可能失败。macOS 可执行: brew install ffmpeg")

    def _set_primary_button_state(self, btn, enabled: bool):
        """设置主按钮启用/禁用状态"""
        try:
            btn.configure(state=("normal" if enabled else "disabled"))
        except Exception:
            pass

    def set_status(self, text: str):
        """设置状态栏文本"""
        self.root.after(0, lambda: self.status_var.set(text))

    def _on_tree_configure(self):
        """表格配置改变时刷新"""
        self.refresh_tree_overlays()

    def estimate_duration_str(self, chars: int) -> str:
        """估算时长字符串"""
        wpm = max(1, self.wpm_var.get())
        seconds = self.duration_estimator.estimate_seconds(chars, wpm)
        return self.seconds_to_str(seconds)

    def seconds_to_str(self, total_seconds: int) -> str:
        """秒数转字符串"""
        if total_seconds < 3600:
            m = total_seconds // 60
            s = total_seconds % 60
            return f"{m}:{s:02d}"
        h = total_seconds // 3600
        m = (total_seconds % 3600) // 60
        s = total_seconds % 60
        return f"{h}:{m:02d}:{s:02d}"

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

    def get_audio_output_dir(self) -> str:
        """
        根据当前 TXT 目录，计算音频输出目录。
        规则：
        - 如果 TXT 目录名以 _txt 结尾，则同级生成 _Audio 目录
        - 否则退化为在 TXT 目录同级生成 “原目录名_Audio”
        """
        txt_dir = self.txt_dir.get().strip()
        if not txt_dir:
            return ""

        txt_dir = os.path.abspath(txt_dir)
        parent_dir = os.path.dirname(txt_dir)
        txt_basename = os.path.basename(txt_dir)

        if txt_basename.endswith("_txt"):
            audio_basename = txt_basename[:-4] + "_Audio"
        else:
            audio_basename = txt_basename + "_Audio"

        return os.path.join(parent_dir, audio_basename)

    def refresh_voices(self):
        """刷新语音列表"""
        def task():
            self.set_status("正在刷新音色列表...")
            voices = self.edge.refresh_voices()

            def apply():
                self.voice_combo.configure(values=voices)
                if voices:
                    current = self.voice_var.get()
                    if current not in voices:
                        self.voice_var.set(voices[0])
                self.set_status(f"音色列表已刷新，共 {len(voices)} 个")

            self.root.after(0, apply)

        import threading
        threading.Thread(target=task, daemon=True).start()

    def toggle_merge_options(self, initial: bool = False):
        """控制目标时长设置的显示/隐藏"""
        if self.merge_var.get():
            if not self.target_row.winfo_ismapped():
                self.target_row.pack(side="left")
        else:
            if self.target_row.winfo_ismapped():
                self.target_row.pack_forget()

        if not initial:
            self.update_idletasks()

    def update_idletasks(self):
        try:
            self.root.update_idletasks()
        except Exception:
            pass

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
            self.update_action_buttons_state()
            self._refresh_dir_snapshot()

    def read_text_file(self, path: str) -> Optional[str]:
        """读取文本文件"""
        try:
            with open(path, "r", encoding="utf-8", errors="ignore") as f:
                return f.read()
        except Exception:
            return None

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
        """状态动画刻度"""
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
                    text=text,
                    voice=self.voice_var.get(),
                    speed=self.speed_var.get(),
                    pitch=self.pitch_var.get(),
                    volume=self.volume_var.get(),
                    output_file=tmp_file
                )

                self.set_status("试听文件生成成功，开始播放...")

                if platform.system() == "Darwin":
                    subprocess.run(["afplay", tmp_file], check=True, capture_output=True)
                elif platform.system() == "Windows":
                    proc = subprocess.Popen(
                        ["start", "/wait", tmp_file],
                        shell=True,
                        stdout=subprocess.PIPE,
                        stderr=subprocess.PIPE
                    )
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
                self.root.after(
                    0,
                    lambda msg=err_msg: messagebox.showerror(
                        "试听失败",
                        f"无法连接到语音服务。\n\n错误信息：\n{msg}"
                    )
                )
            finally:
                if tmp_file and os.path.exists(tmp_file):
                    try:
                        os.remove(tmp_file)
                    except Exception:
                        pass

        import threading
        threading.Thread(target=worker, daemon=True).start()

    def open_output_dir(self):
        """打开输出音频目录"""
        txt_dir = self.txt_dir.get().strip()
        if not txt_dir or not os.path.isdir(txt_dir):
            messagebox.showerror("错误", "请先选择有效的TXT目录")
            return

        out_dir = self.get_audio_output_dir()
        if not out_dir:
            messagebox.showerror("错误", "无法确定音频输出目录")
            return

        os.makedirs(out_dir, exist_ok=True)

        if platform.system() == "Darwin":
            os.system(f'open "{out_dir}"')
        elif platform.system() == "Windows":
            os.startfile(out_dir)
        else:
            os.system(f'xdg-open "{out_dir}"')

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
            max_chars_per_file = 200000

            out_dir, converted_count, total_files = convert_epub_to_txt(
                path,
                progress_callback=lambda s: self.set_status(f"EPUB：{s}"),
                max_chars_per_file=max_chars_per_file
            )

            self.txt_dir.set(out_dir)
            self.config_mgr.set("last_txt_dir", out_dir)
            self.load_file_list(out_dir)
            self.update_action_buttons_state()
            self._refresh_dir_snapshot()
            self.set_status(f"EPUB转换完成：生成 {converted_count} 个TXT / {total_files} 个章节")

        except Exception as e:
            messagebox.showerror("EPUB转换失败", str(e))
            self.set_status("EPUB转换失败")

    def on_closing(self):
        """窗口关闭时保存配置"""
        self.stop_flag = True

        if self.dir_watch_job is not None:
            try:
                self.root.after_cancel(self.dir_watch_job)
            except Exception:
                pass
            self.dir_watch_job = None

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