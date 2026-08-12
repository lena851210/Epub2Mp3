import unittest
import tempfile
from pathlib import Path
from unittest.mock import Mock, patch

from main import AudiobookGenerator


class MainHelperTests(unittest.TestCase):
    @patch("main.tk.Tk")
    @patch("main.TkinterDnD")
    def test_drag_extension_failure_falls_back_to_normal_window(self, dnd_mock, tk_mock):
        fallback_root = object()
        dnd_mock.Tk.side_effect = RuntimeError("Unable to load tkdnd library")
        tk_mock.return_value = fallback_root

        root, drag_available = AudiobookGenerator._create_root_window()

        self.assertIs(root, fallback_root)
        self.assertFalse(drag_available)
        tk_mock.assert_called_once_with()

    def test_drop_paths_keep_spaces_and_chinese_names(self):
        root = Mock()
        root.tk.splitlist.return_value = (
            "/tmp/一本 书.epub",
            "/tmp/第二本.epub",
        )

        paths = AudiobookGenerator.parse_drop_paths(
            root,
            "{/tmp/一本 书.epub} /tmp/第二本.epub",
        )

        self.assertEqual(paths, ["/tmp/一本 书.epub", "/tmp/第二本.epub"])

    def test_no_audio_error_is_translated_to_actionable_chinese(self):
        message = AudiobookGenerator.format_tts_error(
            RuntimeError(
                "No audio was received. Please verify that your parameters are correct."
            )
        )

        self.assertIn("没有返回音频", message)
        self.assertIn("自动重试 3 次", message)
        self.assertIn("刷新列表", message)

    def test_source_epub_directory_prefers_matching_epub(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            epub_path = root / "测试书.epub"
            epub_path.write_bytes(b"epub")
            txt_dir = root / "测试书_txt"

            resolved = AudiobookGenerator.resolve_source_epub_dir(
                str(txt_dir),
                str(epub_path),
            )

            self.assertEqual(resolved, str(root))

    def test_source_epub_directory_falls_back_to_txt_parent(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            missing_txt_dir = Path(temp_dir) / "旧版书籍_txt"

            resolved = AudiobookGenerator.resolve_source_epub_dir(
                str(missing_txt_dir),
                "",
            )

            self.assertEqual(resolved, temp_dir)


if __name__ == "__main__":
    unittest.main()
