# Load transport/native asyncio modules before SoundCard is imported while a
# frozen capture worker is unpickled. proc-tap imports asyncio in that worker.
import asyncio  # noqa: F401 -- preload Windows native dependencies before SoundCard
import multiprocessing
import multiprocessing.connection
import os
import sys

# Frozen releases intentionally bundle WASAPI PortAudio without ASIO. Even
# SD_ENABLE_ASIO=0 selects the absent ASIO DLL in sounddevice, so ignore an
# inherited setting in this process only. Source installations are unchanged.
if getattr(sys, "frozen", False):
    os.environ.pop("SD_ENABLE_ASIO", None)


def main():
    from PyQt6.QtWidgets import QApplication

    # Qt must initialize the GUI thread's COM apartment before importing
    # SoundCard (through gui), which otherwise initializes it as MTA.
    app = QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(False)

    from gui import TrayApplication

    app.tray = TrayApplication(app)

    sys.exit(app.exec())


if __name__ == "__main__":
    multiprocessing.freeze_support()
    main()
