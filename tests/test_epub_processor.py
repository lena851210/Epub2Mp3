import tempfile
import unittest
from pathlib import Path

from ebooklib import epub

from epub_processor import convert_epub_to_txt


class EpubProcessorTests(unittest.TestCase):
    def test_short_toc_chapters_stay_separate(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_root = Path(temp_dir)
            epub_path = temp_root / "短章节测试.epub"
            self._build_sample_epub(epub_path)

            out_dir, converted_count, total_files = convert_epub_to_txt(
                str(epub_path),
                max_chars_per_file=200000,
            )

            txt_files = sorted(Path(out_dir).glob("*.txt"))
            self.assertEqual(converted_count, 2)
            self.assertEqual(total_files, 2)
            self.assertEqual(len(txt_files), 2)
            self.assertIn("第一章 出发", txt_files[0].name)
            self.assertIn("第二章 抵达", txt_files[1].name)

            first_text = txt_files[0].read_text(encoding="utf-8")
            second_text = txt_files[1].read_text(encoding="utf-8")
            self.assertIn("这是第一章的第一段", first_text)
            self.assertNotIn("这是第二章的正文", first_text)
            self.assertIn("这是第二章的正文", second_text)

    @staticmethod
    def _build_sample_epub(target: Path):
        book = epub.EpubBook()
        book.set_identifier("short-toc-chapters-test")
        book.set_title("短章节测试")
        book.set_language("zh-CN")

        chapter_one = epub.EpubHtml(
            title="第一章 出发",
            file_name="chapter_1.xhtml",
            lang="zh-CN",
        )
        chapter_one.content = """
        <html><body>
          <h1>第一章 出发</h1>
          <p>这是第一章的第一段。</p>
          <p>这是第一章的第二段。</p>
        </body></html>
        """

        chapter_two = epub.EpubHtml(
            title="第二章 抵达",
            file_name="chapter_2.xhtml",
            lang="zh-CN",
        )
        chapter_two.content = """
        <html><body>
          <h1>第二章 抵达</h1>
          <p>这是第二章的正文。</p>
        </body></html>
        """

        book.add_item(chapter_one)
        book.add_item(chapter_two)
        book.toc = (chapter_one, chapter_two)
        book.spine = ["nav", chapter_one, chapter_two]
        book.add_item(epub.EpubNcx())
        book.add_item(epub.EpubNav())
        epub.write_epub(str(target), book)


if __name__ == "__main__":
    unittest.main()
