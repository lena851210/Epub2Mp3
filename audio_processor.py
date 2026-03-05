# -*- coding: utf-8 -*-
"""
模块3：音频处理 - 文本预处理、TTS 合成、MP3 合并
"""

import os
import re
import time
import shutil
from typing import List, Tuple, Callable, Optional

from pydub import AudioSegment

from models import sanitize_filename


# ====== 【函数1】文本预处理 ======
def preprocess_text(text: str, max_length: int = 500) -> List[str]:
    """
    将长文本按句子拆分为多个短段落
    
    ���数:
        text: 输入文本
        max_length: 单段最大字符数
    
    返回:
        短文本列表
    """
    if len(text) <= max_length:
        return [text]

    paras = []
    current = ""
    sentences = re.split(r'(?<=[。！？\.\!\?])\s+', text)

    for sentence in sentences:
        sentence = sentence.strip()
        if not sentence:
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

    return paras if paras else [text]


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
    tts_with_retry
):
    """
    处理一个音频块（包含文本合成和音频合并）
    
    参数:
        text: 要合成的文本
        out_dir: 输出目录
        part_num: 分段编号
        file_list: 源文件列表
        edge_tts_wrapper: TTS 包装器
        voice_var: 音色变量
        speed_var: 语速变量
        pitch_var: 音调变量
        volume_var: 音量变量
        set_file_status: 设置文件状态回调
        set_file_progress: 设置进度回调
        set_error: 设置错误回调
        get_mp3_duration_str: 获取 MP3 时长回调
        seconds_to_str: 秒数转字符串回调
        stop_flag_check: 检查停止标志回调
        tts_with_retry: TTS 转换重试回调
    
    返回:
        输出文件路径或 None
    """
    if stop_flag_check():
        for name in file_list:
            set_file_status(name, "已中断", spinning=False)
        return None

    # 🟢 生成输出文件路径
    if len(file_list) == 1:
        base_name = sanitize_filename(os.path.splitext(file_list[0])[0])
        # 如果是分割片段（part_num > 1），只在末尾加 _pN
        if part_num > 1:
            opath = os.path.join(out_dir, f"{base_name}_p{part_num}.mp3")
        else:
            opath = os.path.join(out_dir, f"{base_name}.mp3")
    else:
        first_file = sanitize_filename(os.path.splitext(file_list[0])[0])
        last_file = sanitize_filename(os.path.splitext(file_list[-1])[0])
        # 多文件合并也只在末尾加 _pN
        if part_num > 1:
            opath = os.path.join(out_dir, f"{first_file}_到_{last_file}_p{part_num}.mp3")
        else:
            opath = os.path.join(out_dir, f"{first_file}_到_{last_file}.mp3")

    # 🟢 检查文件是否已存在
    if os.path.exists(opath):
        dur = get_mp3_duration_str(opath)
        for name in file_list:
            set_file_progress(name, 100.0)
            if dur:
                set_file_status(name, f"已存在(跳过)（🕒{dur}）", spinning=False)
            else:
                set_file_status(name, "已存在(跳过)", spinning=False)
        return opath

    # 🟢 预处理文本，拆分为多个段落
    paras = preprocess_text(text)
    total_paras = max(1, len(paras))

    leader = file_list[0]
    for idx, name in enumerate(file_list):
        if idx == 0:
            set_file_status(name, f"合并段落 {part_num}：已拆分为 {total_paras} 段", spinning=True)
        else:
            set_file_status(name, f"等待合并（{part_num}，{total_paras} 段）", spinning=False)
        set_file_progress(name, 0.0)

    tempfiles = []
    ok = True

    # 🟢 对每个段落进行 TTS 合成
    for j, p in enumerate(paras, 1):
        if stop_flag_check():
            ok = False
            for name in file_list:
                set_file_status(name, "已中断", spinning=False)
            break

        tfile = os.path.join(out_dir, f"temp_part{part_num}_{j}.mp3")
        bn = os.path.basename(tfile)

        set_file_status(leader, f"合成中 {bn} ({j}/{total_paras})", spinning=True)
        for name in file_list[1:]:
            set_file_status(name, f"合并中（{j}/{total_paras}）", spinning=False)

        success = tts_with_retry(p, tfile, iid_for_error=file_list[0], max_retries=3)
        if not success:
            ok = False
            set_file_status(leader, f"合成失败 {bn}", spinning=False)
            for name in file_list[1:]:
                set_file_status(name, "失败：合并中断", spinning=False)
            break

        tempfiles.append(tfile)
        percent = j / total_paras * 100.0
        for name in file_list:
            set_file_progress(name, percent)

    # 🟢 合并所有临时 MP3 文件
    if ok and not stop_flag_check():
        for name in file_list:
            set_file_status(name, f"合并音频（{len(tempfiles)} 段）", spinning=(name == leader))

        try:
            dur_str = None
            if len(tempfiles) == 1:
                # 只有一个临时文件，直接移动
                shutil.move(tempfiles[0], opath)
                dur_str = get_mp3_duration_str(opath)
            else:
                # 多个临时文件，需要合并
                combined = AudioSegment.empty()
                for tf in tempfiles:
                    audio = AudioSegment.from_file(tf, format="mp3")
                    combined += audio
                    combined += AudioSegment.silent(duration=1000)  # 添加 1 秒静音

                for name in file_list:
                    set_file_status(name, "导出 MP3...", spinning=(name == leader))

                combined.export(opath, format="mp3")
                dur_str = seconds_to_str(int(round(len(combined) / 1000)))

            # 清理临时文件
            for tf in tempfiles:
                if os.path.exists(tf):
                    try:
                        os.remove(tf)
                    except Exception:
                        pass

            # 更新状态
            for name in file_list:
                set_file_progress(name, 100.0)
                if dur_str:
                    set_file_status(name, f"✅ 已完成，🕒{dur_str}", spinning=False)
                else:
                    set_file_status(name, "✅ 已完成", spinning=False)

            return opath

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
            return None
    else:
        # 清理临时文件
        for tf in tempfiles:
            try:
                os.remove(tf)
            except Exception:
                pass
        return None