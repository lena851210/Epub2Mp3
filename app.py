# -*- coding: utf-8 -*-
"""
程序入口 - 启动有声书生成工具
"""

from main import AudiobookGenerator

if __name__ == "__main__":
    app = AudiobookGenerator()
    app.run()