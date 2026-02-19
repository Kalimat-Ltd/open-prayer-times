import multiprocessing
from pathlib import Path
import sys
import tkinter as tk

if __package__ in (None, ""):
    project_root = Path(__file__).resolve().parents[3]
    if str(project_root) not in sys.path:
        sys.path.insert(0, str(project_root))

from src.app.presentation.prayer_times_gui import PrayerApp, _lazy_imports


def main() -> int:
    multiprocessing.freeze_support()
    _lazy_imports()
    root = tk.Tk()
    PrayerApp(root)
    root.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
