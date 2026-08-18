import unittest

from file_manager import (
    display_task_progress,
    display_task_status,
    display_txt_name,
    is_selection_column_hit,
    resolve_scanned_selection,
)


class FileManagerDisplayTests(unittest.TestCase):
    def test_txt_suffix_is_hidden_only_in_display_name(self):
        filename = "001 前言.txt"
        self.assertEqual(display_txt_name(filename), "001 前言")
        self.assertEqual(filename, "001 前言.txt")

    def test_internal_status_is_simplified_for_users(self):
        raw = "合成中 __tmp_40723_1786521548183_5.mp3 (2/2)"
        self.assertEqual(display_task_status(raw), "正在生成语音")
        self.assertNotIn("tmp", display_task_status(raw))
        self.assertEqual(display_task_progress(raw), "2 / 2")

    def test_terminal_statuses_are_consistent(self):
        self.assertEqual(display_task_status("已完成（时长3:44，含书籍信息）"), "✅ 已完成")
        self.assertEqual(display_task_status("已存在(2段)"), "↪ 已跳过")
        self.assertEqual(display_task_status("失败：无法读取文件"), "❌ 生成失败")
        self.assertEqual(display_task_status("已中断"), "■ 已停止")
        self.assertEqual(display_task_status("等待合并（25 段）"), "等待处理")

    def test_treeview_selection_column_accepts_tree_region_clicks(self):
        self.assertTrue(is_selection_column_hit("tree", "#0"))
        self.assertTrue(is_selection_column_hit("cell", "#0"))
        self.assertFalse(is_selection_column_hit("cell", "#1"))
        self.assertFalse(is_selection_column_hit("heading", "#0"))

    def test_background_scan_does_not_override_manual_selection(self):
        self.assertFalse(resolve_scanned_selection(False, True, user_overridden=True))
        self.assertTrue(resolve_scanned_selection(True, False, user_overridden=True))
        self.assertTrue(resolve_scanned_selection(False, True, user_overridden=False))


if __name__ == "__main__":
    unittest.main()
