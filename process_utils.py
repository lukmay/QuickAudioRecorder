"""Windows helpers for choosing which application owns system-audio capture.

The tray app only ever needs read-only window/process metadata, so every
dependency here is optional: on a machine without ``pywin32`` or ``psutil``
the module still imports and simply reports that no applications are
available, rather than taking the recorder down with it.
"""

import os

try:
    import psutil
except ImportError:  # pragma: no cover - depends on the local install
    psutil = None

try:
    import win32gui
    import win32process
except ImportError:  # pragma: no cover - non-Windows or no pywin32
    win32gui = None
    win32process = None


def _available():
    return None not in (psutil, win32gui, win32process)


def get_root_pid(pid):
    """Return the top-most parent of ``pid``.

    Browsers and other sandboxed apps play audio from a child process, so
    capturing only the selected PID misses most of the sound. Walking up to
    the root owner captures the whole tree.
    """
    if not _available():
        return pid
    try:
        process = psutil.Process(int(pid))
        while process.parent() is not None:
            process = process.parent()
        return process.pid
    except (psutil.NoSuchProcess, psutil.AccessDenied, ValueError):
        return pid


def get_foreground_pid():
    """Return the PID that owns the foreground window, or None."""
    if not _available():
        return None
    try:
        hwnd = win32gui.GetForegroundWindow()
        if not hwnd:
            return None
        return win32process.GetWindowThreadProcessId(hwnd)[1]
    except Exception:
        return None


def get_active_applications():
    """Return window-level choices, including handles needed for previews."""
    if not _available():
        return []

    windows = {}

    def visit(hwnd, _):
        try:
            if not win32gui.IsWindowVisible(hwnd):
                return True
            left, top, _, _ = win32gui.GetWindowRect(hwnd)
            if left <= -32000 or top <= -32000:
                return True
            title = win32gui.GetWindowText(hwnd).strip()
            if not title:
                child = win32gui.GetWindow(hwnd, 5)  # GW_CHILD
                while child:
                    title = win32gui.GetWindowText(child).strip()
                    if title:
                        break
                    child = win32gui.GetWindow(child, 2)  # GW_HWNDNEXT
            pid = win32process.GetWindowThreadProcessId(hwnd)[1]
            if pid == os.getpid():
                return True
            process = psutil.Process(pid)
            name = process.name()
            try:
                created = process.create_time()
            except Exception:
                created = 0.0
            windows[hwnd] = {"pid": pid, "name": name, "title": title or name,
                             "hwnd": hwnd, "create_time": created}
        except Exception:
            return True
        return True

    win32gui.EnumWindows(visit, None)
    return sorted(windows.values(), key=lambda app: app["title"].casefold())


def get_foreground_window_info():
    """Return (hwnd, pid) of the foreground window using user32 directly.

    ctypes is preferred here over the win32 wrappers because it reads the live
    foreground state at call time, which makes window ordering ("most recently
    interacted with") accurate even between the recency timer's ticks. Returns
    (0, None) when nothing is foregrounded or the call fails.
    """
    try:
        import ctypes
        from ctypes import wintypes

        user32 = ctypes.windll.user32
        hwnd = user32.GetForegroundWindow()
        if not hwnd:
            return (0, None)
        pid = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        return (hwnd, pid.value)
    except Exception:
        return (0, None)
