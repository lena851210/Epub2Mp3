import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from audio_processor import (
    _process_audio_chunk,
    build_audio_metadata,
    embed_cover_art,
    embed_mp3_metadata,
)


class Value:
    def __init__(self, value):
        self.value = value

    def get(self):
        return self.value


class AudioProcessorTests(unittest.TestCase):
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

    def test_audio_metadata_uses_sequential_track_titles(self):
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
                return True

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

            self.assertIsNotNone(output_path)
            embed_mock.assert_called_once_with(
                output_path,
                cover_path=str(cover_path),
                metadata={
                    "title": "001 测试",
                    "album": "测试书",
                    "artist": "测试作者",
                },
            )
            self.assertTrue(any("含书籍信息" in status for status in statuses))


if __name__ == "__main__":
    unittest.main()
