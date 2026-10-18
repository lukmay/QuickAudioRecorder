"""Multiple recording outputs are copied in one clipboard operation."""
import subprocess
import types

from PyQt6.QtWidgets import QApplication

import clipboard_utils


def test_powershell_receives_all_paths(tmp_path, monkeypatch):
    paths = [tmp_path / "one's.wav", tmp_path / "two.wav"]
    for path in paths:
        path.touch()
    calls = []

    def run(cmd, **kwargs):
        calls.append(cmd)
        return types.SimpleNamespace(returncode=0)

    monkeypatch.setattr(subprocess, "run", run)
    success, _ = clipboard_utils.copy_file_to_clipboard(paths)
    assert success
    assert len(calls) == 1
    assert "one''s.wav" in calls[0][-1]
    assert "two.wav" in calls[0][-1]
    assert "-LiteralPath" in calls[0][-1]


def test_qt_fallback_receives_all_paths(tmp_path, monkeypatch):
    _app = QApplication.instance() or QApplication([])
    paths = [tmp_path / "one.wav", tmp_path / "two.wav"]
    for path in paths:
        path.touch()
    expected_urls = [str(p).replace("\\", "/") for p in paths]

    def fail(*args, **kwargs):
        raise OSError("PowerShell unavailable")

    monkeypatch.setattr(subprocess, "run", fail)

    class Clipboard:
        mime_data = None

        def setMimeData(self, data):
            self.mime_data = data

    clipboard = Clipboard()
    monkeypatch.setattr(clipboard_utils.QApplication, "clipboard", lambda: clipboard)
    success, _ = clipboard_utils.copy_file_to_clipboard(paths)
    assert success
    assert clipboard.mime_data is not None
    urls = clipboard.mime_data.urls()
    assert [url.toLocalFile() for url in urls] == expected_urls
