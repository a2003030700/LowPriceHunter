"""Low Price Hunter 啟動器（同時支援原始碼執行與 PyInstaller 打包）。"""

import os
import sys

if getattr(sys, "frozen", False):
    BASE = os.path.dirname(sys.executable)
else:
    BASE = os.path.dirname(os.path.abspath(__file__))
    sys.path.insert(0, BASE)

os.makedirs(os.path.join(BASE, "data"), exist_ok=True)

if "--no-browser" in sys.argv:
    os.environ["PS5_UI_NO_BROWSER"] = "1"

if getattr(sys, "frozen", False):
    _log = open(os.path.join(BASE, "data", "ui_boot.log"), "a", encoding="utf-8", buffering=1)
    sys.stdout = _log
    sys.stderr = _log

from ps5hunter.webui import main  # noqa: E402

if __name__ == "__main__":
    main(8765)
