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
import time
import threading
from typing import Optional

from pydub import AudioSegment

from models import (
    ConfigManager,
    DurationEstimator,
    EdgeTTSWrapper,
    VOICE_MAPPING,
    BASE_WORDS_PER_MINUTE,
    DEFAULT_PITCH,
    DEFAULT_SPEED,
    DEFAULT_VOICE_NAME,
    DEFAULT_VOLUME,
    DEFAULT_WORDS_PER_MINUTE,
)
from epub_processor import convert_epub_to_txt
from generation_manager import GenerationMixin
from file_manager import FileManagerMixin, display_task_progress, display_task_status

try:
    from tkinterdnd2 import DND_FILES, TkinterDnD
except ImportError:
    DND_FILES = None
    TkinterDnD = None


class AudiobookGenerator(FileManagerMixin, GenerationMixin):
    """有声书生成工具 - 主应用类"""

    @staticmethod
    def _create_checkbox_image(root, checked: bool):
        """创建随 Treeview 行滚动的系统风格 checkbox 图标。"""
        size = 18
        image = tk.PhotoImage(master=root, width=size, height=size)
        image.blank()

        def inside_rounded_rect(x, y, inset, radius):
            left = top = inset
            right = bottom = size - 1 - inset
            nearest_x = min(max(x, left + radius), right - radius)
            nearest_y = min(max(y, top + radius), bottom - radius)
            return (
                left <= x <= right
                and top <= y <= bottom
                and (x - nearest_x) ** 2 + (y - nearest_y) ** 2 <= radius ** 2
            )

        for y in range(size):
            for x in range(size):
                if checked and inside_rounded_rect(x, y, 1, 4):
                    image.put("#0A84FF", (x, y))
                elif not checked and inside_rounded_rect(x, y, 2, 3):
                    color = "#FFFFFF" if inside_rounded_rect(x, y, 3, 2) else "#8E8E93"
                    image.put(color, (x, y))

        if checked:
            # 以两段粗线绘制白色 checkmark，尺寸接近 macOS 原生 checkbox。
            segments = ((4.7, 9.2, 7.7, 12.1), (7.5, 12.0, 13.6, 5.8))
            for y in range(size):
                for x in range(size):
                    for x1, y1, x2, y2 in segments:
                        dx, dy = x2 - x1, y2 - y1
                        length_sq = dx * dx + dy * dy
                        t = max(0.0, min(1.0, ((x - x1) * dx + (y - y1) * dy) / length_sq))
                        px, py = x1 + t * dx, y1 + t * dy
                        if (x - px) ** 2 + (y - py) ** 2 <= 1.15 ** 2:
                            image.put("#FFFFFF", (x, y))
                            break
        return image

    @staticmethod
    def _create_root_window():
        """创建主窗口；拖放扩展异常时保证 App 仍能启动。"""
        if TkinterDnD is not None:
            try:
                return TkinterDnD.Tk(), True
            except Exception as e:
                print(f"文件拖放暂不可用，已回退到普通窗口: {e}")
        return tk.Tk(), False

    def __init__(self):
        self.config_mgr = ConfigManager(self._get_config_path())
        self.edge = EdgeTTSWrapper()
        self.duration_estimator = DurationEstimator(BASE_WORDS_PER_MINUTE)

        self.root, self.drag_and_drop_available = self._create_root_window()
        self.root.title("EPUB to MP3 - V2.0")
        self.root.geometry("960x680")
        self.root.minsize(720, 560)
        self.stop_flag = False
        self.tts_cancel_event = threading.Event()
        self.is_generating = False
        self.generation_thread = None
        self.exit_after_stop = False
        self.is_previewing = False
        self.is_importing_epub = False
        self.task_files = []
        self.task_statuses = {}
        self.task_progress = {}
        self.task_started_at = None
        self.elapsed_timer_job = None
        self.last_elapsed_seconds = 0

        # UI 状态管理
        self.selection_states = {}
        self.file_chars = {}
        self.error_detail = {}

        # 动画效果
        self.spinner_frames = ["⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏"]
        self.spinner_active = {}
        self.spinner_job = None

        # 目录监测
        self.dir_watch_job = None
        self.last_dir_snapshot = None

        self.create_ui()
        self._setup_epub_drop()
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
        style.configure("Treeview", rowheight=28, indent=0)
        button_padding = (10, 7, 10, 5) if sysname == "Darwin" else (10, 6)
        primary_padding = (12, 8, 12, 6) if sysname == "Darwin" else (12, 7)
        style.configure("App.TButton", font=base_font, padding=button_padding, anchor="center")
        style.configure(
            "Primary.TButton",
            font=(base_font[0], base_font[1], "bold"),
            padding=primary_padding,
            anchor="center",
        )
        style.configure(
            "ImportPrimary.TButton",
            font=(base_font[0], base_font[1], "bold"),
            padding=primary_padding,
            anchor="center",
            foreground="white",
            background="#1677FF",
        )
        style.map(
            "ImportPrimary.TButton",
            foreground=[("disabled", "#E6E6E6"), ("!disabled", "white")],
            background=[("pressed", "#0958D9"), ("active", "#4096FF"), ("!disabled", "#1677FF")],
        )
        style.configure(
            "Accent.TButton",
            font=(base_font[0], base_font[1], "bold"),
            padding=primary_padding,
            anchor="center",
        )
        # 去掉原生 Toolbutton 的白色方框，只保留克制的重置图标。
        style.layout(
            "ResetIcon.TButton",
            [("Button.padding", {"sticky": "nswe", "children": [("Button.label", {"sticky": "nswe"})]})],
        )
        style.configure(
            "ResetIcon.TButton",
            font=(base_font[0], 13, "normal"),
            foreground="#6E6E73",
            padding=(4, 1),
            anchor="center",
        )
        style.map(
            "ResetIcon.TButton",
            foreground=[("disabled", "#B8B8B8"), ("pressed", "#0958D9"), ("active", "#1677FF")],
        )

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
            text="📘 导入 EPUB",
            command=self.import_epub,
            width=20,
            style=("Accent.TButton" if sysname == "Darwin" else "ImportPrimary.TButton")
        )
        self.import_epub_btn.pack(side="right")

        columns = ("name", "est", "size", "chars", "status", "progress")
        self.files_tree = ttk.Treeview(files_lf, columns=columns, show="tree headings")
        self.checkbox_unchecked_image = self._create_checkbox_image(self.root, checked=False)
        self.checkbox_checked_image = self._create_checkbox_image(self.root, checked=True)
        self.files_tree.heading("#0", text="选择", anchor="center")
        self.files_tree.heading("name", text="章节")
        self.files_tree.heading("est", text="预估时长")
        self.files_tree.heading("size", text="大小(KB)")
        self.files_tree.heading("chars", text="字数")
        self.files_tree.heading("status", text="状态")
        self.files_tree.heading("progress", text="进度")

        self.files_tree.column("#0", width=52, minwidth=44, anchor="center", stretch=False)
        self.files_tree.column("name", width=330, minwidth=250, anchor="w", stretch=True)
        self.files_tree.column("est", width=90, minwidth=82, anchor="center", stretch=False)
        self.files_tree.column("size", width=80, minwidth=78, anchor="center", stretch=False)
        self.files_tree.column("chars", width=80, minwidth=78, anchor="center", stretch=False)
        self.files_tree.column("status", width=145, minwidth=140, anchor="center", stretch=False)
        self.files_tree.column("progress", width=95, minwidth=90, anchor="center", stretch=False)

        vsb = ttk.Scrollbar(files_lf, orient="vertical", command=self.files_tree.yview)
        hsb = ttk.Scrollbar(files_lf, orient="horizontal", command=self.files_tree.xview)

        def update_horizontal_scrollbar(first, last):
            """内容完整可见时隐藏横向滚动条，窗口过窄时再自动出现。"""
            hsb.set(first, last)
            if float(first) <= 0.0 and float(last) >= 1.0:
                hsb.grid_remove()
            else:
                hsb.grid()

        self.files_tree.configure(
            yscrollcommand=vsb.set,
            xscrollcommand=update_horizontal_scrollbar,
        )
        self.files_tree.grid(row=2, column=0, sticky="nsew")
        vsb.grid(row=2, column=1, sticky="ns")
        hsb.grid(row=3, column=0, sticky="ew")
        self.root.after_idle(
            lambda: update_horizontal_scrollbar(*self.files_tree.xview())
        )

        self.files_tree.bind("<Double-1>", self.on_tree_double_click)
        self.files_tree.bind("<Button-1>", self._on_tree_click)

        # =========================
        # Step 2：语音设置
        # =========================
        voice_lf = ttk.LabelFrame(main, text="Step 2：语音设置", padding=(10, 8))
        voice_lf.grid(row=1, column=0, sticky="ew", pady=(0, 8))
        voice_lf.columnconfigure(1, weight=1)

        ttk.Label(voice_lf, text="音色:").grid(row=0, column=0, padx=(0, 8), pady=(0, 6), sticky="w")

        configured_voice = self.config_mgr.get("edge", {}).get("voice_name", DEFAULT_VOICE_NAME)
        default_voice_label = next(
            (label for label, code in VOICE_MAPPING.items() if code == DEFAULT_VOICE_NAME),
            "云健(男)",
        )
        current_voice = next(
            (label for label, code in VOICE_MAPPING.items() if code == configured_voice),
            default_voice_label,
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

        ttk.Button(
            voice_lf,
            text="刷新列表",
            command=self.refresh_voices,
            width=10,
            style="App.TButton",
        ).grid(row=0, column=2, padx=(0, 6), pady=(0, 6), sticky="e")
        self.preview_btn = ttk.Button(
            voice_lf,
            text="试听",
            command=self.preview_audio,
            width=8,
            style="App.TButton",
        )
        self.preview_btn.grid(row=0, column=3, pady=(0, 6), sticky="e")

        sliders = ttk.Frame(voice_lf)
        sliders.grid(row=1, column=0, columnspan=4, sticky="ew", pady=(4, 0))
        for i in range(3):
            sliders.columnconfigure(i, weight=1)

        def make_slider(
            parent,
            label_text,
            var,
            from_,
            to,
            value_text_func,
            reset_value,
        ):
            frame = ttk.Frame(parent)

            def build_label():
                return value_text_func(var.get())

            label_var = tk.StringVar(value=build_label())
            label_row = ttk.Frame(frame)
            label_row.pack(fill="x", pady=(0, 2))
            ttk.Label(label_row, text=f"{label_text}:").pack(side="left")
            ttk.Label(label_row, textvariable=label_var, anchor="w").pack(side="left", padx=(6, 0))
            ttk.Button(
                label_row,
                text="↺",
                command=lambda: var.set(reset_value),
                width=1,
                cursor="pointinghand",
                style="ResetIcon.TButton",
            ).pack(side="left", padx=(5, 0))
            scale = ttk.Scale(frame, from_=from_, to=to, variable=var, orient="horizontal")
            scale.pack(fill="x")

            def refresh_label(*args):
                label_var.set(build_label())

            var.trace_add("write", refresh_label)
            return frame

        def speed_text(value):
            if value < 0.75:
                description = "慢"
            elif value <= 1.15:
                description = "适中"
            else:
                description = "快"
            estimated_wpm = max(1, int(BASE_WORDS_PER_MINUTE * value))
            return f"{description} · {value:.1f}x（约 {estimated_wpm} 字/分）"

        def pitch_text(value):
            if value < -5:
                description = "低沉"
            elif value > 5:
                description = "明亮"
            else:
                description = "自然"
            return f"{description} {value:+.0f}Hz"

        def volume_text(value):
            return f"{value:+.0f}%"

        self.speed_var = tk.DoubleVar(value=self.config_mgr.get("edge", {}).get("speed", DEFAULT_SPEED))
        speed_frame = make_slider(
            sliders,
            "语速",
            self.speed_var,
            0.5,
            2.0,
            speed_text,
            DEFAULT_SPEED,
        )
        speed_frame.grid(row=0, column=0, sticky="ew", padx=(0, 24))

        self.pitch_var = tk.DoubleVar(value=self.config_mgr.get("edge", {}).get("pitch", DEFAULT_PITCH))
        make_slider(sliders, "音调", self.pitch_var, -50, 50, pitch_text, DEFAULT_PITCH).grid(
            row=0, column=1, sticky="ew", padx=(0, 24)
        )

        self.volume_var = tk.DoubleVar(value=self.config_mgr.get("edge", {}).get("volume", DEFAULT_VOLUME))
        make_slider(sliders, "音量", self.volume_var, -100, 100, volume_text, DEFAULT_VOLUME).grid(
            row=0, column=2, sticky="ew"
        )

        def update_wpm(*args):
            estimated_wpm = max(1, int(BASE_WORDS_PER_MINUTE * self.speed_var.get()))
            self.wpm_var.set(estimated_wpm)
            self.update_all_estimates()

        self.wpm_var = tk.IntVar(value=self.config_mgr.get("words_per_minute", DEFAULT_WORDS_PER_MINUTE))
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
            text="按目标时长组织 MP3",
            variable=self.merge_var,
            command=self.toggle_merge_options
        )
        self.merge_check.pack(side="left")
        ttk.Label(
            merge_row,
            text="（短章节合并，超长章节拆分）",
            foreground="#666"
        ).pack(side="left", padx=(6, 0))

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
        ttk.Button(
            dir_row,
            text="浏览...",
            command=self.select_input_dir,
            width=8,
            style="App.TButton",
        ).grid(row=0, column=2, sticky="e")

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
        ttk.Button(
            left_btns,
            text="📁 打开音频目录",
            command=self.open_output_dir,
            width=16,
            style="App.TButton",
        ).pack(side="left")

        right_btns = ttk.Frame(action_bar)
        right_btns.grid(row=0, column=1, sticky="e")
        self.stop_btn = ttk.Button(
            right_btns,
            text="停止",
            command=self.stop_generation,
            width=10,
            state="disabled",
            style="App.TButton",
        )
        self.stop_btn.pack(side="left", padx=(0, 10))

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
        status_bar.columnconfigure(0, weight=1)

        status_text_row = ttk.Frame(status_bar)
        status_text_row.grid(row=0, column=0, sticky="ew", pady=(0, 4))
        status_text_row.columnconfigure(0, weight=1)

        self.status_var = tk.StringVar(value="就绪")
        ttk.Label(
            status_text_row,
            textvariable=self.status_var,
            anchor="w"
        ).grid(row=0, column=0, sticky="ew")

        self.overall_progress_text_var = tk.StringVar(value="整体进度：未开始")
        ttk.Label(
            status_text_row,
            textvariable=self.overall_progress_text_var,
            anchor="e"
        ).grid(row=0, column=1, sticky="e", padx=(12, 0))

        self.elapsed_time_var = tk.StringVar(value="总耗时：00:00:00")
        ttk.Label(
            status_text_row,
            textvariable=self.elapsed_time_var,
            anchor="e",
            foreground="#666",
        ).grid(row=0, column=2, sticky="e", padx=(12, 0))

        self.overall_progress_var = tk.DoubleVar(value=0.0)
        self.overall_progress_bar = ttk.Progressbar(
            status_bar,
            orient="horizontal",
            mode="determinate",
            maximum=100.0,
            variable=self.overall_progress_var
        )
        self.overall_progress_bar.grid(row=1, column=0, sticky="ew")

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

    @staticmethod
    def resolve_source_epub_dir(txt_dir: str, last_epub_path: str) -> str:
        """在 TXT 目录失效时，定位当前转换对象 EPUB 所在目录。

        优先使用与当前 ``*_txt`` 目录对应的最后一本 EPUB；
        旧版配置没有 EPUB 路径时，回退到 TXT 目录的上级目录。
        """
        normalized_txt_dir = os.path.abspath(txt_dir) if txt_dir else ""
        normalized_epub = os.path.abspath(last_epub_path) if last_epub_path else ""

        if normalized_epub and os.path.isfile(normalized_epub):
            expected_txt_dir = os.path.splitext(normalized_epub)[0] + "_txt"
            if not normalized_txt_dir or normalized_txt_dir == expected_txt_dir:
                return os.path.dirname(normalized_epub)

        if normalized_txt_dir:
            parent_dir = os.path.dirname(normalized_txt_dir)
            if os.path.isdir(parent_dir):
                return parent_dir

        return ""

    @staticmethod
    def _open_directory_path(directory: str):
        """使用当前系统打开目录。"""
        if platform.system() == "Darwin":
            subprocess.run(["open", directory], check=False)
        elif platform.system() == "Windows":
            os.startfile(directory)
        else:
            subprocess.run(["xdg-open", directory], check=False)

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
        """保留内部状态供统计使用，列表只显示面向用户的状态。"""
        def _apply():
            display_status = display_task_status(status_text)
            step_progress = display_task_progress(status_text)
            if spinning:
                self.spinner_active[iid] = {"base": display_status, "idx": 0}
                if self.spinner_job is None:
                    self.spinner_job = self.root.after(120, self._spinner_tick)
            else:
                if iid in self.spinner_active:
                    del self.spinner_active[iid]
                self.files_tree.set(iid, "status", display_status)

            if step_progress:
                self.files_tree.set(iid, "progress", step_progress)
            elif display_status in {"✅ 已完成", "↪ 已跳过"}:
                self.files_tree.set(iid, "progress", "100%")
            elif display_status in {"等待处理", "❌ 生成失败", "■ 已停止"}:
                self.files_tree.set(iid, "progress", "—")

            if iid in getattr(self, "task_files", []):
                self.task_statuses[iid] = status_text
                self.update_overall_progress()

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
        if self.is_previewing:
            self.set_status("试听正在生成中，请稍候...")
            return

        voice_name = self.voice_var.get()
        speed = self.speed_var.get()
        pitch = self.pitch_var.get()
        volume = self.volume_var.get()
        text = f"你好，我是你的有声书助手，现在是{voice_name}为您朗读。"

        self.is_previewing = True
        self.preview_btn.configure(state="disabled")
        self.set_status("正在生成试听音频...")

        def worker():
            tmp_file = None
            try:
                with tempfile.NamedTemporaryFile(delete=False, suffix=".mp3") as tmp:
                    tmp_file = tmp.name

                last_error = None
                max_attempts = 3

                for attempt in range(1, max_attempts + 1):
                    try:
                        if os.path.exists(tmp_file):
                            os.remove(tmp_file)

                        self.edge.text_to_speech(
                            text=text,
                            voice=voice_name,
                            speed=speed,
                            pitch=pitch,
                            volume=volume,
                            output_file=tmp_file
                        )

                        if os.path.exists(tmp_file) and os.path.getsize(tmp_file) > 0:
                            last_error = None
                            break
                        raise RuntimeError("语音服务未生成有效音频文件")
                    except Exception as e:
                        last_error = e
                        if attempt < max_attempts:
                            self.set_status(
                                f"试听暂时失败，正在自动重试（{attempt + 1}/{max_attempts}）..."
                            )
                            time.sleep(0.6 * attempt)

                if last_error is not None:
                    raise last_error

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
                friendly_message = self.format_tts_error(e)
                self.set_status("试听失败，请按提示重试。")
                self.root.after(
                    0,
                    lambda msg=friendly_message: messagebox.showerror(
                        "试听失败",
                        msg
                    )
                )
            finally:
                if tmp_file and os.path.exists(tmp_file):
                    try:
                        os.remove(tmp_file)
                    except Exception:
                        pass

                def finish_preview():
                    self.is_previewing = False
                    self.preview_btn.configure(state="normal")

                try:
                    self.root.after(0, finish_preview)
                except Exception:
                    pass

        import threading
        threading.Thread(target=worker, daemon=True).start()

    @staticmethod
    def format_tts_error(error: Exception) -> str:
        """把 Edge TTS 的技术错误转换成普通用户能执行的提示。"""
        technical_message = str(error).strip() or error.__class__.__name__
        lower_message = technical_message.lower()

        if "no audio was received" in lower_message:
            return (
                "语音服务本次没有返回音频，程序已自动重试 3 次。\n\n"
                "你可以这样处理：\n"
                "1. 点击「刷新列表」后重新选择音色；\n"
                "2. 保持语速、音调和音量为默认值再试听；\n"
                "3. 如果仍然失败，等待几分钟后再试。\n\n"
                f"技术信息：{technical_message}"
            )

        return (
            "试听音频生成失败，程序已自动重试 3 次。\n\n"
            "请检查网络，或点击「刷新列表」后换一个音色再试。\n\n"
            f"技术信息：{technical_message}"
        )

    def open_output_dir(self):
        """打开输出音频目录"""
        txt_dir = self.txt_dir.get().strip()
        if not txt_dir or not os.path.isdir(txt_dir):
            source_dir = self.resolve_source_epub_dir(
                txt_dir,
                self.config_mgr.get("last_epub_path", ""),
            )
            if source_dir:
                self._open_directory_path(source_dir)
                self.set_status("TXT目录已不存在，已打开原 EPUB 所在目录")
                return

            messagebox.showerror("错误", "未找到有效的 TXT 目录或原 EPUB 所在目录")
            return

        out_dir = self.get_audio_output_dir()
        if not out_dir:
            messagebox.showerror("错误", "无法确定音频输出目录")
            return

        os.makedirs(out_dir, exist_ok=True)

        self._open_directory_path(out_dir)

    @staticmethod
    def parse_drop_paths(root, raw_data: str):
        """解析 Finder / Explorer 拖放数据，兼容中文、空格和多文件。"""
        if not raw_data:
            return []
        try:
            return [str(path) for path in root.tk.splitlist(raw_data) if str(path).strip()]
        except Exception:
            return [str(raw_data).strip()] if str(raw_data).strip() else []

    def _setup_epub_drop(self):
        """让整个主窗口接收 EPUB 文件拖放。"""
        if (
            not self.drag_and_drop_available
            or DND_FILES is None
            or not hasattr(self.root, "drop_target_register")
        ):
            return
        try:
            self.root.drop_target_register(DND_FILES)
            self.root.dnd_bind("<<DragEnter>>", self._on_epub_drag_enter)
            self.root.dnd_bind("<<DragLeave>>", self._on_epub_drag_leave)
            self.root.dnd_bind("<<Drop>>", self._on_epub_drop)
        except Exception as e:
            print(f"文件拖放初始化失败: {e}")

    def _on_epub_drag_enter(self, _event):
        if self.is_generating or self.is_importing_epub:
            self.set_status("当前任务进行中，暂时不能导入新的 EPUB")
        else:
            self.set_status("松开以导入 EPUB")
        return "copy"

    def _on_epub_drag_leave(self, _event):
        if not self.is_generating and not self.is_importing_epub:
            self.set_status("就绪")

    def _on_epub_drop(self, event):
        paths = self.parse_drop_paths(self.root, getattr(event, "data", ""))
        if self.is_generating or self.is_importing_epub:
            messagebox.showwarning("暂时无法导入", "当前任务进行中，请等待完成或停止后再拖入 EPUB。")
            return "break"
        if len(paths) != 1:
            messagebox.showwarning("无法导入", "一次请只拖入一本 EPUB。")
            self.set_status("一次请只拖入一本 EPUB")
            return "break"
        self._import_epub_path(paths[0])
        return "break"

    def import_epub(self):
        """通过文件选择器导入 EPUB。"""
        if self.is_generating or self.is_importing_epub:
            messagebox.showwarning("暂时无法导入", "当前任务进行中，请等待完成或停止后再导入 EPUB。")
            return
        path = filedialog.askopenfilename(
            title="选择 EPUB",
            filetypes=[("EPUB 文件", "*.epub"), ("所有文件", "*.*")]
        )
        if not path:
            return
        self._import_epub_path(path)

    def _import_epub_path(self, path: str):
        """处理已选定的 EPUB 路径；按钮与拖放共用同一转换入口。"""
        path = os.path.abspath(os.path.expanduser(str(path or "").strip()))
        if not path.lower().endswith(".epub"):
            messagebox.showerror("格式错误", "只支持导入 .epub 文件。")
            self.set_status("导入失败：不是 EPUB 文件")
            return
        if not os.path.isfile(path):
            messagebox.showerror("文件不存在", "未找到拖入的 EPUB 文件。")
            self.set_status("导入失败：文件不存在")
            return

        self.is_importing_epub = True
        self.update_action_buttons_state()
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
            self.config_mgr.set("last_epub_path", path)
            self.load_file_list(out_dir)
            self.update_action_buttons_state()
            self._refresh_dir_snapshot()
            self.set_status(f"EPUB转换完成：生成 {converted_count} 个TXT / {total_files} 个章节")

        except Exception as e:
            messagebox.showerror("EPUB转换失败", str(e))
            self.set_status("EPUB转换失败")
        finally:
            self.is_importing_epub = False
            self.update_action_buttons_state()

    def on_closing(self):
        """运行中关闭先确认，并等待当前任务安全停止。"""
        if self.is_generating:
            if self.exit_after_stop:
                self.set_status("正在安全停止当前任务，请稍候...")
                return

            should_exit = messagebox.askyesno(
                "停止任务并退出？",
                "转换仍在进行，确定要停止任务并退出吗？\n\n"
                "已经成功生成的 MP3 会保留，当前未完成的片段将被清理。",
                icon="warning",
            )
            if not should_exit:
                return

            self.exit_after_stop = True
            self.stop_generation()
            self.set_status("正在安全停止当前任务，完成后将自动退出...")
            return

        self._close_app()

    def _close_app(self):
        """保存配置并关闭窗口；只在后台任务已退出后调用。"""
        self.stop_flag = True
        self.tts_cancel_event.set()

        if self.elapsed_timer_job is not None:
            try:
                self.root.after_cancel(self.elapsed_timer_job)
            except Exception:
                pass
            self.elapsed_timer_job = None

        if self.dir_watch_job is not None:
            try:
                self.root.after_cancel(self.dir_watch_job)
            except Exception:
                pass
            self.dir_watch_job = None

        edge_voice_name = VOICE_MAPPING.get(self.voice_var.get(), DEFAULT_VOICE_NAME)
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
