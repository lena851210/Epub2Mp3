# -*- coding: utf-8 -*-
"""
模块3：音频处理 - 文本预处理、TTS 合成、MP3 合并
"""

import os
import re
import shutil
import subprocess
import tempfile
import time
from typing import Dict, List, Tuple, Optional

from pydub import AudioSegment

from models import TTSResult

from models import sanitize_filename


def _parse_num_and_title(stem: str) -> Tuple[Optional[int], str]:
    """
    从类似 '001-第一章' / '001 第一章' 解析出 (1, '第一章')
    解析失败则 (None, 原始stem)
    """
    s = (stem or "").strip()
    m = re.match(r"^\s*(\d{1,4})\s*[-_ ]\s*(.+?)\s*$", s)
    if m:
        return int(m.group(1)), m.group(2).strip()
    m = re.match(r"^\s*(\d{1,4})\s*(.+?)\s*$", s)
    if m:
        return int(m.group(1)), m.group(2).strip(" -_")
    return None, s


def get_original_chapter_number(filename: str) -> Optional[int]:
    """从 TXT 文件名读取 EPUB 解析阶段已经确定的原始章节序号。"""
    stem = os.path.splitext(os.path.basename(filename or ""))[0]
    chapter_number, _title = _parse_num_and_title(stem)
    return chapter_number


def _short_title(title: str, max_len: int = 20) -> str:
    """缩短标题，避免文件名过长"""
    t = (title or "").strip()
    if len(t) > max_len:
        t = t[:max_len].rstrip()
    return t or "无标题"


def build_output_path(out_dir: str, file_list: List[str], part_num: int, split_total: int = 1) -> str:
    """
    命名规则：

    单文件：
      - 不切分：001 章节名.mp3
      - 切分：  001-1 章节名.mp3、001-2 章节名.mp3 ...

    多文件合并：
      - 001-010 第一章_到_第十章.mp3
    """
    stems = [sanitize_filename(os.path.splitext(f)[0]) for f in file_list]

    # ===== 单文件 =====
    if len(stems) == 1:
        stem = stems[0].strip()
        chap_no, title = _parse_num_and_title(stem)

        chap_str = f"{chap_no:03d}" if chap_no is not None else ""
        title = title.strip() or stem or "无标题"

        if split_total and split_total > 1:
            base = f"{chap_str}-{int(part_num)} {title}".strip()
        else:
            base = f"{chap_str} {title}".strip() if chap_str else title

        base = sanitize_filename(base, max_len=140)
        return os.path.join(out_dir, base + ".mp3")

    # ===== 多文件合并 =====
    first_stem = stems[0]
    last_stem = stems[-1]
    n1, t1 = _parse_num_and_title(first_stem)
    n2, t2 = _parse_num_and_title(last_stem)

    if n1 is not None and n2 is not None:
        range_part = f"{n1:03d}-{n2:03d}"
        title_part = f"{_short_title(t1)}_到_{_short_title(t2)}"
        base = sanitize_filename(f"{range_part} {title_part}", max_len=140)
        return os.path.join(out_dir, base + ".mp3")

    base = sanitize_filename(f"{_short_title(first_stem)}_到_{_short_title(last_stem)}", max_len=140)
    return os.path.join(out_dir, base + ".mp3")


def build_single_file_output_candidates(out_dir: str, txt_filename: str) -> List[str]:
    """
    根据单个 TXT 文件名，推导可能对应的正式 MP3 文件：
    1) 未拆分时：001 标题.mp3
    2) 拆分时：001-1 标题.mp3、001-2 标题.mp3 ...（这里只做“前缀匹配”）

    返回候选正式路径列表（第一个通常是未拆分文件的精确路径）。
    """
    exact_path = build_output_path(out_dir, [txt_filename], part_num=1, split_total=1)
    return [exact_path]


def find_existing_outputs_for_txt(out_dir: str, txt_filename: str) -> List[str]:
    """
    查找某个 TXT 在“单文件模式”下已经存在的正式输出 MP3。
    规则：
    - 精确匹配未拆分文件：001 标题.mp3
    - 或匹配拆分文件前缀：001-1 标题.mp3 / 001-2 标题.mp3 ...
    - 忽略 __tmp_*.mp3 临时文件
    """
    if not out_dir or not os.path.isdir(out_dir):
        return []

    exact_candidates = build_single_file_output_candidates(out_dir, txt_filename)
    exact_path = exact_candidates[0] if exact_candidates else ""

    found: List[str] = []

    if exact_path and os.path.exists(exact_path) and os.path.isfile(exact_path):
        found.append(exact_path)

    stem = sanitize_filename(os.path.splitext(txt_filename)[0]).strip()
    chap_no, title = _parse_num_and_title(stem)
    chap_str = f"{chap_no:03d}" if chap_no is not None else ""
    title = sanitize_filename(title.strip() or stem or "无标题")

    # 匹配拆分后的正式文件：001-1 标题.mp3 / 001-2 标题.mp3 ...
    if chap_str:
        split_prefix = f"{chap_str}-"
        split_title_part = f" {title}.mp3"

        try:
            for fn in os.listdir(out_dir):
                if not fn.lower().endswith(".mp3"):
                    continue
                if fn.startswith("__tmp_"):
                    continue
                if fn.startswith(split_prefix) and fn.endswith(split_title_part):
                    full = os.path.join(out_dir, fn)
                    if os.path.isfile(full) and full not in found:
                        found.append(full)
        except Exception:
            pass

    found.sort()
    return found


# ====== 【函数1】文本预处理：更自然的断句/按标题拆分 ======
def preprocess_text(text: str, max_length: int = 500) -> List[str]:
    """
    更自然的分段策略：
    1) 先按空行分段（段落）
    2) 段落内若出现明显“标题行”，标题单独成段
    3) 对过长段落按句末标点拆句（不依赖空格）
    4) 再把句子打包回 <= max_length
    5) 最后仍超长才硬切
    """
    if not text:
        return []

    t = text.replace("\r\n", "\n").replace("\r", "\n")
    t = re.sub(r"\n{3,}", "\n\n", t).strip()

    heading_re = re.compile(
        r"^(第[0-9一二三四五六七八九十百千万零〇两]+[章节回卷篇部].{0,30}|Chapter\s+\d+.*|CHAPTER\s+\d+.*)$",
        re.IGNORECASE
    )

    def looks_like_heading_line(line: str) -> bool:
        ln = (line or "").strip()
        if not ln:
            return False
        if heading_re.match(ln) and len(ln) <= 60:
            return True
        if len(ln) <= 25 and not any(c in ln for c in "。，；！？.!?"):
            if any(k in ln for k in ("章", "节", "篇", "回", "卷", "部")):
                return True
        return False

    units: List[str] = []
    for para in re.split(r"\n{2,}", t):
        para = para.strip()
        if not para:
            continue

        lines = [ln.strip() for ln in para.split("\n") if ln.strip()]
        buf: List[str] = []
        for ln in lines:
            if looks_like_heading_line(ln):
                if buf:
                    units.append(" ".join(buf).strip())
                    buf = []
                units.append(ln)
            else:
                buf.append(ln)
        if buf:
            units.append(" ".join(buf).strip())

    sentences: List[str] = []
    for u in units:
        if len(u) <= max_length:
            sentences.append(u)
            continue
        parts = re.split(r"(?<=[。！？.!?])", u)
        parts = [p.strip() for p in parts if p and p.strip()]
        sentences.extend(parts if parts else [u])

    chunks: List[str] = []
    cur = ""
    for s in sentences:
        if not s:
            continue
        if not cur:
            cur = s
            continue

        if len(cur) + 1 + len(s) <= max_length:
            joiner = "\n" if cur.endswith(("。", "！", "？", ".", "!", "?")) else " "
            cur = cur + joiner + s
        else:
            chunks.append(cur.strip())
            cur = s
    if cur:
        chunks.append(cur.strip())

    final: List[str] = []
    for c in chunks:
        if len(c) <= max_length:
            final.append(c)
        else:
            for i in range(0, len(c), max_length):
                seg = c[i:i + max_length].strip()
                if seg:
                    final.append(seg)

    return final


def build_audio_metadata(
    book_metadata: Optional[Dict[str, object]],
    file_list: List[str],
    part_num: int = 1,
    split_total: int = 1,
    track_number: Optional[int] = None,
) -> Dict[str, str]:
    """组装精简 MP3 标签：Title 使用调用方明确传入的音轨序号。"""
    metadata = dict(book_metadata or {})
    track_titles = metadata.get("tracks", {})
    if not isinstance(track_titles, dict):
        track_titles = {}
    titles: List[str] = []
    for filename in file_list:
        basename = os.path.basename(filename)
        title = str(track_titles.get(basename, "") or "").strip()
        if not title:
            stem = os.path.splitext(basename)[0]
            # 手动导入普通 TXT 时没有隐藏映射，才退回到文件名。
            match = re.match(r"^\s*\d{1,4}(?:-\d{1,4})?\s*[-_ ]*\s*(.+?)\s*$", stem)
            title = match.group(1) if match else stem
        cleaned = re.sub(r"\s+", " ", title).strip()
        if cleaned and cleaned not in titles:
            titles.append(cleaned)

    if len(titles) == 1:
        title = titles[0]
    elif len(titles) > 1:
        title = f"{titles[0]} — {titles[-1]}"
    else:
        title = ""

    sequence = track_number if track_number is not None else part_num
    if title and sequence and sequence > 0:
        title = f"{int(sequence):03d} {title}"

    return {
        "title": title,
        "album": str(metadata.get("album", "") or "").strip(),
        "artist": str(metadata.get("artist", "") or "").strip(),
    }


def embed_mp3_metadata(
    mp3_path: str,
    cover_path: Optional[str] = None,
    metadata: Optional[Dict[str, str]] = None,
) -> bool:
    """
    使用 FFmpeg 一次写入封面和 Title / Album / Artist。

    先生成临时文件，成功后再替换原音频；失败时保留原 MP3 不变。
    """
    clean_metadata = {
        key: re.sub(r"\s+", " ", str((metadata or {}).get(key, "") or "")).strip()
        for key in ("title", "album", "artist")
    }
    has_cover = bool(cover_path and os.path.isfile(cover_path))
    if not mp3_path or (not has_cover and not any(clean_metadata.values())):
        return False
    if not os.path.isfile(mp3_path):
        return False
    ffmpeg_path = shutil.which("ffmpeg")
    if not ffmpeg_path:
        return False

    temp_file = tempfile.NamedTemporaryFile(
        prefix="__cover_",
        suffix=".mp3",
        dir=os.path.dirname(mp3_path) or ".",
        delete=False,
    )
    temp_path = temp_file.name
    temp_file.close()

    try:
        command = [
            ffmpeg_path, "-y", "-hide_banner", "-loglevel", "error",
            "-i", mp3_path,
        ]
        if has_cover:
            command.extend(["-i", cover_path])
        command.extend(["-map", "0:a:0"])
        if has_cover:
            command.extend([
                "-map", "1:v:0", "-c:v", "mjpeg",
                "-metadata:s:v", "title=Album cover",
                "-metadata:s:v", "comment=Cover (front)",
                "-disposition:v", "attached_pic",
            ])
        command.extend(["-c:a", "copy", "-map_metadata", "-1", "-id3v2_version", "3"])
        for key in ("title", "album", "artist"):
            if clean_metadata[key]:
                command.extend(["-metadata", f"{key}={clean_metadata[key]}"])
        command.append(temp_path)

        subprocess.run(
            command,
            check=True,
            capture_output=True,
        )
        if not os.path.isfile(temp_path) or os.path.getsize(temp_path) <= 0:
            return False
        os.replace(temp_path, mp3_path)
        return True
    except Exception:
        return False
    finally:
        if os.path.exists(temp_path):
            try:
                os.remove(temp_path)
            except Exception:
                pass


def embed_cover_art(mp3_path: str, cover_path: str) -> bool:
    """兼容旧调用：只写入封面。"""
    return embed_mp3_metadata(mp3_path, cover_path=cover_path)


# ====== 【函数2】音频处理核心函数 ======
def _process_audio_chunk(
    text,
    out_dir,
    part_num,
    file_list,
    edge_tts_wrapper,
    voice_var,
    speed_var,
    pitch_var,
    volume_var,
    set_file_status,
    set_file_progress,
    set_error,
    get_mp3_duration_str,
    seconds_to_str,
    stop_flag_check,
    tts_with_retry,
    split_total: int = 1,
    cover_path: Optional[str] = None,
    book_metadata: Optional[Dict[str, str]] = None,
    track_number: Optional[int] = None,
):
    """
    处理一个音频块（包含文本合成和音频合并）
    """
    if stop_flag_check():
        for name in file_list:
            set_file_status(name, "已中断", spinning=False)
        return TTSResult.STOPPED

    opath = build_output_path(out_dir, file_list, part_num, split_total=split_total)

    # 已存在则跳过（只针对正式输出文件）
    if os.path.exists(opath):
        dur = get_mp3_duration_str(opath)
        for name in file_list:
            set_file_progress(name, 100.0)
            if dur:
                set_file_status(name, f"已存在(跳过)（时长{dur}）", spinning=False)
            else:
                set_file_status(name, "已存在(跳过)", spinning=False)
        return TTSResult.SUCCESS

    paras = preprocess_text(text)
    total_paras = max(1, len(paras))

    leader = file_list[0]
    for idx, name in enumerate(file_list):
        if idx == 0:
            set_file_status(name, f"准备合成：共 {total_paras} 段", spinning=True)
        else:
            set_file_status(name, f"等待合并（{total_paras} 段）", spinning=False)
        set_file_progress(name, 0.0)

    tempfiles: List[str] = []
    ok = True

    for j, p in enumerate(paras, 1):
        if stop_flag_check():
            ok = False
            for name in file_list:
                set_file_status(name, "已中断", spinning=False)
            break

        tfile = os.path.join(out_dir, f"__tmp_{os.getpid()}_{int(time.time()*1000)}_{j}.mp3")
        bn = os.path.basename(tfile)

        set_file_status(leader, f"合成中 {bn} ({j}/{total_paras})", spinning=True)
        for name in file_list[1:]:
            set_file_status(name, f"合并中（{j}/{total_paras}）", spinning=False)

        tts_result = tts_with_retry(p, tfile, iid_for_error=file_list[0], max_retries=3)
        if tts_result == TTSResult.STOPPED:
            try:
                os.remove(tfile)
            except Exception:
                pass
            ok = False
            for name in file_list:
                set_file_status(name, "已中断", spinning=False)
            break
        if tts_result != TTSResult.SUCCESS:
            try:
                os.remove(tfile)
            except Exception:
                pass
            ok = False
            set_file_status(leader, f"合成失败 {bn}", spinning=False)
            for name in file_list[1:]:
                set_file_status(name, "失败：合并中断", spinning=False)
            break

        tempfiles.append(tfile)
        percent = j / total_paras * 100.0
        for name in file_list:
            set_file_progress(name, percent)

    if ok and not stop_flag_check():
        for name in file_list:
            set_file_status(name, f"合并音频（{len(tempfiles)} 段）", spinning=(name == leader))

        try:
            dur_str = None

            if len(tempfiles) == 1:
                shutil.move(tempfiles[0], opath)
                dur_str = get_mp3_duration_str(opath)
            else:
                combined = AudioSegment.empty()
                for tf in tempfiles:
                    audio = AudioSegment.from_file(tf, format="mp3")
                    combined += audio
                    combined += AudioSegment.silent(duration=800)

                for name in file_list:
                    set_file_status(name, "导出 MP3...", spinning=(name == leader))

                combined.export(opath, format="mp3")
                dur_str = seconds_to_str(int(round(len(combined) / 1000)))

            for tf in tempfiles:
                if os.path.exists(tf):
                    try:
                        os.remove(tf)
                    except Exception:
                        pass

            metadata_written = None
            audio_metadata = build_audio_metadata(
                book_metadata,
                file_list,
                part_num=part_num,
                split_total=split_total,
                track_number=track_number,
            )
            if cover_path or any(audio_metadata.values()):
                for name in file_list:
                    set_file_status(name, "正在写入书籍信息...", spinning=(name == leader))
                metadata_written = embed_mp3_metadata(
                    opath,
                    cover_path=cover_path,
                    metadata=audio_metadata,
                )

            status_details = []
            if dur_str:
                status_details.append(f"时长{dur_str}")
            if metadata_written is True:
                status_details.append("含书籍信息")
            elif metadata_written is False:
                status_details.append("书籍信息未写入")

            completed_status = "已完成"
            if status_details:
                completed_status += f"（{'，'.join(status_details)}）"

            for name in file_list:
                set_file_progress(name, 100.0)
                set_file_status(name, completed_status, spinning=False)

            return TTSResult.SUCCESS

        except Exception as e:
            set_error(file_list[0], e)
            for name in file_list:
                set_file_status(name, "失败：合并/导出错误", spinning=False)
            for tf in tempfiles:
                if os.path.exists(tf):
                    try:
                        os.remove(tf)
                    except Exception:
                        pass
            return TTSResult.FAILED

    for tf in tempfiles:
        try:
            os.remove(tf)
        except Exception:
            pass
    return TTSResult.STOPPED if stop_flag_check() else TTSResult.FAILED
