import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from audio_processor import (
    _process_audio_chunk,
    build_audio_metadata,
    build_output_path,
    embed_cover_art,
    embed_mp3_metadata,
    get_original_chapter_number,
    normalize_chinese_numbers_for_tts,
    preprocess_text,
)
from models import TTSResult


class Value:
    def __init__(self, value):
        self.value = value

    def get(self):
        return self.value


class AudioProcessorTests(unittest.TestCase):
    def test_chinese_number_normalization_uses_context(self):
        source = (
            "这件事发生在2000年前。公元2000年，他刚好20岁。"
            "会议日期是2024年5月20日，共有2000人参加，完成度为10%，"
            "相关内容见第2000章，测量结果是3.14米，背景始于20世纪。"
        )

        normalized = normalize_chinese_numbers_for_tts(source)

        self.assertIn("两千年前", normalized)
        self.assertIn("公元二零零零年", normalized)
        self.assertIn("二十岁", normalized)
        self.assertIn("二零二四年五月二十日", normalized)
        self.assertIn("两千人", normalized)
        self.assertIn("百分之十", normalized)
        self.assertIn("第二千章", normalized)
        self.assertIn("三点一四米", normalized)
        self.assertIn("二十世纪", normalized)

    def test_ambiguous_identifiers_are_left_unchanged(self):
        source = "版本V2.1，编号2000，ISBN 978-7-111-12345-6。"

        self.assertEqual(normalize_chinese_numbers_for_tts(source), source)

    def test_number_normalization_only_changes_tts_chunks(self):
        source = "公元2000年，距今已有2000年。"

        chunks = preprocess_text(source)

        self.assertEqual(source, "公元2000年，距今已有2000年。")
        self.assertEqual(chunks, ["公元二零零零年，距今已有两千年。"])

    @patch("audio_processor.shutil.which", return_value="/usr/local/bin/ffmpeg")
    @patch("audio_processor.subprocess.run")
    def test_embed_cover_replaces_audio_only_after_success(self, run_mock, _which_mock):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            mp3_path = root / "chapter.mp3"
            cover_path = root / "cover.jpg"
            mp3_path.write_bytes(b"original-audio")
            cover_path.write_bytes(b"cover-image")

            def fake_ffmpeg(command, **_kwargs):
                Path(command[-1]).write_bytes(b"audio-with-cover")

            run_mock.side_effect = fake_ffmpeg

            result = embed_cover_art(str(mp3_path), str(cover_path))

            self.assertTrue(result)
            self.assertEqual(mp3_path.read_bytes(), b"audio-with-cover")
            self.assertIn("attached_pic", run_mock.call_args.args[0])

    @patch("audio_processor.shutil.which", return_value="/usr/local/bin/ffmpeg")
    @patch("audio_processor.subprocess.run")
    def test_embed_cover_failure_preserves_original_audio(self, run_mock, _which_mock):
        run_mock.side_effect = subprocess.CalledProcessError(1, "ffmpeg")

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            mp3_path = root / "chapter.mp3"
            cover_path = root / "cover.jpg"
            mp3_path.write_bytes(b"original-audio")
            cover_path.write_bytes(b"cover-image")

            result = embed_cover_art(str(mp3_path), str(cover_path))

            self.assertFalse(result)
            self.assertEqual(mp3_path.read_bytes(), b"original-audio")

    def test_original_chapter_number_comes_from_txt_filename(self):
        cases = {
            "001 前言.txt": 1,
            "003 第一章.txt": 3,
            "037 某章节.txt": 37,
            "054-第五十四章.txt": 54,
            "普通章节.txt": None,
        }

        for filename, expected in cases.items():
            with self.subTest(filename=filename):
                self.assertEqual(get_original_chapter_number(filename), expected)

    def test_plain_output_filename_keeps_txt_original_number(self):
        with tempfile.TemporaryDirectory() as out_dir:
            cases = {
                "001 前言.txt": "001 前言.mp3",
                "003 第一章.txt": "003 第一章.mp3",
                "037 某章节.txt": "037 某章节.mp3",
                "054 失败后重试.txt": "054 失败后重试.mp3",
            }

            for txt_filename, expected_mp3 in cases.items():
                with self.subTest(txt_filename=txt_filename):
                    output = build_output_path(out_dir, [txt_filename], part_num=1)
                    self.assertEqual(Path(output).name, expected_mp3)

    def test_audio_metadata_uses_explicit_track_titles(self):
        single = build_audio_metadata(
            {
                "album": "马斯克逻辑",
                "artist": "王煜全",
                "tracks": {
                    "002 理解科技革命.txt": "理解科技革命：马斯克的成功",
                },
            },
            ["002 理解科技革命.txt"],
            track_number=1,
        )
        split = build_audio_metadata(
            {"album": "马斯克逻辑", "artist": "王煜全"},
            ["002 理解科技革命.txt"],
            part_num=2,
            split_total=3,
            track_number=2,
        )
        merged = build_audio_metadata(
            {"album": "马斯克逻辑", "artist": "王煜全"},
            ["002 理解科技革命.txt", "003-1 科技潮流.txt"],
            track_number=3,
        )

        self.assertEqual(single, {
            "title": "001 理解科技革命：马斯克的成功",
            "album": "马斯克逻辑",
            "artist": "王煜全",
        })
        self.assertEqual(split["title"], "002 理解科技革命")
        self.assertEqual(merged["title"], "003 理解科技革命 — 科技潮流")

    @patch("audio_processor.shutil.which", return_value="/usr/local/bin/ffmpeg")
    @patch("audio_processor.subprocess.run")
    def test_mp3_metadata_command_contains_only_non_empty_tags(self, run_mock, _which_mock):
        with tempfile.TemporaryDirectory() as temp_dir:
            mp3_path = Path(temp_dir) / "chapter.mp3"
            mp3_path.write_bytes(b"audio")

            def fake_ffmpeg(command, **_kwargs):
                Path(command[-1]).write_bytes(b"tagged-audio")

            run_mock.side_effect = fake_ffmpeg
            result = embed_mp3_metadata(
                str(mp3_path),
                metadata={"title": "第一章", "album": "书名", "artist": ""},
            )

            command = run_mock.call_args.args[0]
            self.assertTrue(result)
            self.assertIn("title=第一章", command)
            self.assertIn("album=书名", command)
            self.assertNotIn("artist=", command)

    @patch("audio_processor.embed_mp3_metadata", return_value=True)
    def test_final_audio_chunk_receives_saved_book_information(self, embed_mock):
        statuses = []

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            cover_path = root / "cover.jpg"
            cover_path.write_bytes(b"cover-image")

            def tts_with_retry(_text, output_file, **_kwargs):
                Path(output_file).write_bytes(b"audio")
                return TTSResult.SUCCESS

            output_path = _process_audio_chunk(
                text="这是测试正文。",
                out_dir=str(root),
                part_num=1,
                file_list=["001 测试.txt"],
                edge_tts_wrapper=Mock(),
                voice_var=Value("voice"),
                speed_var=Value(1.0),
                pitch_var=Value(0),
                volume_var=Value(0),
                set_file_status=lambda _name, status, **_kwargs: statuses.append(status),
                set_file_progress=lambda *_args, **_kwargs: None,
                set_error=lambda *_args, **_kwargs: None,
                get_mp3_duration_str=lambda _path: "00:01",
                seconds_to_str=lambda seconds: f"00:{seconds:02d}",
                stop_flag_check=lambda: False,
                tts_with_retry=tts_with_retry,
                cover_path=str(cover_path),
                book_metadata={"album": "测试书", "artist": "测试作者"},
            )

            self.assertEqual(output_path, TTSResult.SUCCESS)
            embed_mock.assert_called_once_with(
                build_output_path(str(root), ["001 测试.txt"], 1),
                cover_path=str(cover_path),
                metadata={
                    "title": "001 测试",
                    "album": "测试书",
                    "artist": "测试作者",
                },
            )
            self.assertTrue(any("含书籍信息" in status for status in statuses))

    def test_failed_segment_never_creates_formal_mp3_and_cleans_partial_temp(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)

            def failed_tts(_text, output_file, **_kwargs):
                Path(output_file).write_bytes(b"partial-audio")
                return TTSResult.FAILED

            result = _process_audio_chunk(
                text="这是不能缺失的正文。",
                out_dir=str(root),
                part_num=1,
                file_list=["001 失败测试.txt"],
                edge_tts_wrapper=Mock(),
                voice_var=Value("voice"),
                speed_var=Value(1.0),
                pitch_var=Value(0),
                volume_var=Value(0),
                set_file_status=lambda *_args, **_kwargs: None,
                set_file_progress=lambda *_args, **_kwargs: None,
                set_error=lambda *_args, **_kwargs: None,
                get_mp3_duration_str=lambda _path: "",
                seconds_to_str=lambda _seconds: "",
                stop_flag_check=lambda: False,
                tts_with_retry=failed_tts,
            )

            self.assertEqual(result, TTSResult.FAILED)
            self.assertFalse(Path(build_output_path(str(root), ["001 失败测试.txt"], 1)).exists())
            self.assertEqual(list(root.glob("__tmp_*.mp3")), [])

    def test_existing_successful_mp3_is_preserved(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            output_path = Path(build_output_path(temp_dir, ["001 已完成.txt"], 1))
            output_path.write_bytes(b"completed-audio")

            result = _process_audio_chunk(
                text="不会重新生成。",
                out_dir=temp_dir,
                part_num=1,
                file_list=["001 已完成.txt"],
                edge_tts_wrapper=Mock(),
                voice_var=Value("voice"),
                speed_var=Value(1.0),
                pitch_var=Value(0),
                volume_var=Value(0),
                set_file_status=lambda *_args, **_kwargs: None,
                set_file_progress=lambda *_args, **_kwargs: None,
                set_error=lambda *_args, **_kwargs: None,
                get_mp3_duration_str=lambda _path: "00:10",
                seconds_to_str=lambda _seconds: "",
                stop_flag_check=lambda: False,
                tts_with_retry=Mock(),
            )

            self.assertEqual(result, TTSResult.SUCCESS)
            self.assertEqual(output_path.read_bytes(), b"completed-audio")


if __name__ == "__main__":
    unittest.main()
