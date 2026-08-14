#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
PyInstaller 打包入口。
打包后 backend.py 会通过 --add-data "app:app" 落在 _MEIPASS/app/ 下，
本脚本负责把该目录加入 sys.path，再调用 backend.main()，
从而 backend.py 里 `Path(__file__).parent` 仍能正确找到 index.html/app.js/style.css。
（不改 backend.py 任何代码）
"""
import os
import sys


def main():
    base = getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))
    app_dir = os.path.join(base, "app")
    if os.path.isdir(app_dir):
        sys.path.insert(0, app_dir)
    else:
        sys.path.insert(0, base)
    from backend import main as backend_main
    backend_main()


if __name__ == "__main__":
    main()
