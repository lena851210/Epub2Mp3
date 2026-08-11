import re
import tempfile
import unittest
from pathlib import Path

from ebooklib import epub

from epub_processor import (
    convert_epub_to_txt,
    find_saved_epub_cover,
    remove_leading_title_from_text,
)


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

            cover_path = find_saved_epub_cover(out_dir)
            self.assertIsNotNone(cover_path)
            self.assertEqual(Path(cover_path).read_bytes(), self._sample_cover_bytes())

            first_text = txt_files[0].read_text(encoding="utf-8")
            second_text = txt_files[1].read_text(encoding="utf-8")
            self.assertIn("这是第一章的第一段", first_text)
            self.assertNotIn("这是第二章的正文", first_text)
            self.assertIn("这是第二章的正文", second_text)
            compact_first_text = re.sub(r"\s+", "", first_text)
            self.assertEqual(compact_first_text.count("第一章出发"), 1)

    def test_leading_title_ignores_full_width_spacing(self):
        title = "第一部 不过是另一种大型哺乳类罢了"
        text = (
            "第一部　不过是另一种大型哺乳类罢了\n\n"
            "我们什么时候不再只是另一种大型哺乳类罢了？"
        )

        cleaned = remove_leading_title_from_text(title, text)

        self.assertEqual(cleaned, "我们什么时候不再只是另一种大型哺乳类罢了？")

    def test_matching_sentence_later_in_body_is_preserved(self):
        title = "第一部 不过是另一种大型哺乳类罢了"
        text = (
            "这是正文第一段。\n\n"
            "第一部　不过是另一种大型哺乳类罢了"
        )

        cleaned = remove_leading_title_from_text(title, text)

        self.assertEqual(cleaned, text)

    def test_toc_anchors_in_one_html_become_separate_chapters(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_root = Path(temp_dir)
            epub_path = temp_root / "锚点目录测试.epub"
            self._build_anchor_toc_epub(epub_path)

            out_dir, converted_count, total_files = convert_epub_to_txt(
                str(epub_path),
                max_chars_per_file=200000,
            )

            txt_files = sorted(Path(out_dir).glob("*.txt"))
            self.assertEqual(converted_count, 2)
            self.assertEqual(total_files, 2)
            self.assertEqual(len(txt_files), 2)
            self.assertIn("做一个勇敢的法律人", txt_files[0].name)
            self.assertIn("在自恋中攀登仇恨的高峰", txt_files[1].name)

            first_text = txt_files[0].read_text(encoding="utf-8")
            second_text = txt_files[1].read_text(encoding="utf-8")
            self.assertIn("第一篇的正文", first_text)
            self.assertNotIn("第二篇的正文", first_text)
            self.assertIn("第二篇的正文", second_text)
            self.assertNotIn("版权信息", "\n".join(path.name for path in txt_files))

    @staticmethod
    def _build_sample_epub(target: Path):
        book = epub.EpubBook()
        book.set_identifier("short-toc-chapters-test")
        book.set_title("短章节测试")
        book.set_language("zh-CN")
        book.set_cover("cover.jpg", EpubProcessorTests._sample_cover_bytes())

        chapter_one = epub.EpubHtml(
            title="第一章 出发",
            file_name="chapter_1.xhtml",
            lang="zh-CN",
        )
        chapter_one.content = """
        <html><body>
          <h1>第一章　出发</h1>
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

    @staticmethod
    def _sample_cover_bytes() -> bytes:
        # EPUB 写入和封面提取只需要保留原始图片字节；无需在单元测试中解码图片。
        return b"\xff\xd8\xff\xe0epub-to-mp3-test-cover\xff\xd9"

    @staticmethod
    def _build_anchor_toc_epub(target: Path):
        book = epub.EpubBook()
        book.set_identifier("anchor-toc-test")
        book.set_title("锚点目录测试")
        book.set_language("zh-CN")

        copyright_page = epub.EpubHtml(
            title="版权信息",
            file_name="copyright.xhtml",
            lang="zh-CN",
        )
        copyright_page.content = "<html><body><h1>版权信息</h1><p>不适合朗读。</p></body></html>"

        articles = epub.EpubHtml(
            title="文章合集",
            file_name="articles.xhtml",
            lang="zh-CN",
        )
        articles.content = """
        <html><body>
          <h1>文章合集</h1>
          <h2 id="article-1">做一个勇敢的法律人</h2>
          <p>这是第一篇的正文。</p>
          <h2 id="article-2">在自恋中攀登仇恨的高峰</h2>
          <p>这是第二篇的正文。</p>
        </body></html>
        """

        book.add_item(copyright_page)
        book.add_item(articles)
        section = epub.Section("文章合集", href="articles.xhtml#article-1")
        book.toc = (
            epub.Link("copyright.xhtml", "版权信息", "copyright"),
            (
                section,
                [
                    epub.Link("articles.xhtml#article-1", "做一个勇敢的法律人", "article-1"),
                    epub.Link("articles.xhtml#article-2", "在自恋中攀登仇恨的高峰", "article-2"),
                ],
            ),
        )
        book.spine = ["nav", copyright_page, articles]
        book.add_item(epub.EpubNcx())
        book.add_item(epub.EpubNav())
        epub.write_epub(str(target), book)


if __name__ == "__main__":
    unittest.main()
