import os
import subprocess

from PyQt6.QtCore import QMimeData, QUrl
from PyQt6.QtWidgets import QApplication

def copy_file_to_clipboard(filepath):
    """
    Copies files to the clipboard using PowerShell or Qt fallback.
    Fallback to Qt's clipboard mechanism if PowerShell fails.
    """
    paths = filepath if isinstance(filepath, (list, tuple)) else [filepath]
    paths = [os.path.abspath(path) for path in paths]
    if not paths or not all(os.path.exists(path) for path in paths):
        return False, "File does not exist"

    # Strategy 1: PowerShell (Native / Robust)
    # Set-Clipboard -Path "..." mimics Explorer copy perfectly.
    try:
        cmd = [
            "powershell",
            "-NoProfile",
            "-NonInteractive",
            "-Command",
            "Set-Clipboard -LiteralPath " + ",".join(
                "'" + path.replace("'", "''") + "'" for path in paths)
        ]

        # subprocess.run ensures we wait for it to finish
        result = subprocess.run(cmd, capture_output=True, text=True, creationflags=subprocess.CREATE_NO_WINDOW)

        if result.returncode == 0:
            return True, "Copied via PowerShell"
        else:
            print(f"PowerShell Clipboard Error: {result.stderr}")
            # Don't return yet, try Strategy 2

    except Exception as e:
        print(f"PowerShell execution failed: {e}")

    # Strategy 2: Qt (Fallback)
    try:
        data = QMimeData()
        data.setUrls([QUrl.fromLocalFile(path) for path in paths])
        clipboard = QApplication.clipboard()
        clipboard.setMimeData(data)
        return True, "Copied via Qt (Fallback)"
    except Exception as e:
        print(f"Qt clipboard fallback failed: {e!r}")
        return False, f"All methods failed. Qt Error: {e}"
