"""Windows helpers for choosing which application owns system-audio capture.

The tray app only ever needs read-only window/process metadata, so every
dependency here is optional: on a machine without ``pywin32`` or ``psutil``
the module still imports and simply reports that no applications are
available, rather than taking the recorder down with it.
"""

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
    """Return visible, titled windows as app choices for the settings dialog.

    Windows with no title (tool windows, the shell's own chrome) are skipped,
    and so are the shell host processes that only ever appear as containers
    for real apps. Results are de-duplicated per process and sorted by title.
    """
    if not _available():
        return []

    skip = {"explorer.exe", "applicationframehost.exe"}
    windows = {}

    def visit(hwnd, _):
        if not win32gui.IsWindowVisible(hwnd):
            return True
        title = win32gui.GetWindowText(hwnd).strip()
        if not title:
            return True
        try:
            pid = win32process.GetWindowThreadProcessId(hwnd)[1]
            name = psutil.Process(pid).name()
        except Exception:
            return True
        if name.lower() in skip:
            return True
        windows.setdefault(pid, {"pid": pid, "name": name, "title": title})
        return True

    win32gui.EnumWindows(visit, None)
    return sorted(windows.values(), key=lambda app: app["title"].casefold())
