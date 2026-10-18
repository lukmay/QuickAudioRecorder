import multiprocessing
import sys


def main():
    from PyQt6.QtWidgets import QApplication

    from gui import TrayApplication

    app = QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(False)

    app.tray = TrayApplication(app)

    sys.exit(app.exec())


if __name__ == "__main__":
    multiprocessing.freeze_support()
    main()
