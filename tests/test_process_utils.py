"""Window enumeration preserves the handles required by picker previews."""
import types

import process_utils


def test_choices_keep_each_window_handle(monkeypatch):
    windows = [101, 102, 103, 104]
    api = types.SimpleNamespace(
        EnumWindows=lambda visit, data: [visit(hwnd, data) for hwnd in windows],
        IsWindowVisible=lambda hwnd: True,
        GetWindowRect=lambda hwnd: (-32000, -32000, 0, 0) if hwnd == 103 else (0, 0, 800, 600),
        GetWindowText=lambda hwnd: f"Window {hwnd}",
    )
    monkeypatch.setattr(process_utils, "win32gui", api)
    monkeypatch.setattr(process_utils, "win32process", types.SimpleNamespace(
        GetWindowThreadProcessId=lambda hwnd: (1, 999 if hwnd == 104 else 42)))
    monkeypatch.setattr(process_utils.os, "getpid", lambda: 999)
    monkeypatch.setattr(process_utils, "psutil", types.SimpleNamespace(
        Process=lambda pid: types.SimpleNamespace(name=lambda: "player.exe", create_time=lambda: 123.0)))
    apps = process_utils.get_active_applications()
    assert [app["hwnd"] for app in apps] == [101, 102]
    assert [app["pid"] for app in apps] == [42, 42]
    assert all(app["create_time"] == 123.0 for app in apps)
