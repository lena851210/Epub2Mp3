import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from audio_processor import _process_audio_chunk, embed_cover_art


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

    @patch("audio_processor.embed_cover_art", return_value=True)
    def test_final_audio_chunk_receives_saved_cover(self, embed_mock):
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
            )

            self.assertIsNotNone(output_path)
            embed_mock.assert_called_once_with(output_path, str(cover_path))
            self.assertTrue(any("含封面" in status for status in statuses))


if __name__ == "__main__":
    unittest.main()
