import ctypes
import sys
import os
import json
import shutil
import tempfile
import time
from ctypes import wintypes

import psutil
from PyQt6.QtWidgets import (QApplication, QSystemTrayIcon, QMenu, QMainWindow, 
                             QWidget, QVBoxLayout, QHBoxLayout, QLabel, QComboBox, 
                             QPushButton, QFileDialog, QMessageBox, QGroupBox, 
                             QLineEdit, QFormLayout, QCheckBox, QSpinBox, QScrollArea,
                             QDialog, QListWidget, QListWidgetItem)
from PyQt6.QtGui import QIcon, QAction, QColor, QPixmap, QPainter, QBrush, QKeySequence, QImage
from PyQt6.QtCore import pyqtSignal, QObject, Qt, QUrl, QMimeData, QDir, QEvent, QTimer, QPoint, QSize
import soundcard as sc
import keyboard
from audio_recorder import AudioRecorder, get_devices, get_speakers
from process_utils import get_active_applications, get_foreground_window_info
from clipboard_utils import copy_file_to_clipboard

CONFIG_FILE = "settings.json"

def resource_path(relative_path):
    try:
        base_path = sys._MEIPASS
    except Exception:
        base_path = os.path.abspath(".")
    return os.path.join(base_path, relative_path)

class SignalManager(QObject):
    recording_finished = pyqtSignal(object, str)  # a path, or a list when tracks are separate
    saving = pyqtSignal()
    progress = pyqtSignal(float)
    hotkey = pyqtSignal(str)
    recording_notice = pyqtSignal(str)  # device lost while recording

class HotkeyEdit(QLineEdit):
    """
    Custom widget to capture hotkeys by pressing them.
    Maps Qt events to 'keyboard' library compatible strings.
    """
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setPlaceholderText("Click to set hotkey...")
        self.setReadOnly(True) 
        self.current_sequence = None

    def mousePressEvent(self, event):
        self.setFocus()
        super().mousePressEvent(event)

    def event(self, event):
        # Alt+<key> arrives as a shortcut first and never reaches keyPressEvent
        # unless we claim it here (#3)
        if event.type() == QEvent.Type.ShortcutOverride and self.hasFocus():
            event.accept()
            return True
        return super().event(event)

    def keyPressEvent(self, event):
        key = event.key()
        modifiers = event.modifiers()
        
        if key == Qt.Key.Key_Backspace or key == Qt.Key.Key_Delete:
            self.clear()
            self.current_sequence = None
            return
            
        if key == Qt.Key.Key_Escape:
            self.clearFocus()
            return

        if key in (Qt.Key.Key_Control, Qt.Key.Key_Shift, Qt.Key.Key_Alt, Qt.Key.Key_Meta):
            return

        parts = []
        if modifiers & Qt.KeyboardModifier.ControlModifier: parts.append("ctrl")
        if modifiers & Qt.KeyboardModifier.ShiftModifier:   parts.append("shift")
        if modifiers & Qt.KeyboardModifier.AltModifier:     parts.append("alt")
        if modifiers & Qt.KeyboardModifier.MetaModifier:    parts.append("windows")

        key_text = ""
        if key >= 0x20 and key <= 0x7E:
            key_text = chr(key).lower()
        else:
            key_map = {
                Qt.Key.Key_F1: "f1", Qt.Key.Key_F2: "f2", Qt.Key.Key_F3: "f3", Qt.Key.Key_F4: "f4",
                Qt.Key.Key_F5: "f5", Qt.Key.Key_F6: "f6", Qt.Key.Key_F7: "f7", Qt.Key.Key_F8: "f8",
                Qt.Key.Key_F9: "f9", Qt.Key.Key_F10: "f10", Qt.Key.Key_F11: "f11", Qt.Key.Key_F12: "f12",
                Qt.Key.Key_Left: "left", Qt.Key.Key_Right: "right", Qt.Key.Key_Up: "up", Qt.Key.Key_Down: "down",
                Qt.Key.Key_Space: "space", Qt.Key.Key_Tab: "tab", Qt.Key.Key_Return: "enter", Qt.Key.Key_Enter: "enter",
                Qt.Key.Key_Backspace: "backspace", Qt.Key.Key_Delete: "delete", Qt.Key.Key_Insert: "insert",
                Qt.Key.Key_Home: "home", Qt.Key.Key_End: "end", Qt.Key.Key_PageUp: "pageup", Qt.Key.Key_PageDown: "pagedown",
                Qt.Key.Key_CapsLock: "capslock", Qt.Key.Key_NumLock: "numlock", Qt.Key.Key_ScrollLock: "scrolllock",
                Qt.Key.Key_Print: "print_screen", Qt.Key.Key_Pause: "pause"
            }
            key_text = key_map.get(key)
            if not key_text:
                try: key_text = QKeySequence(key).toString().lower()
                except: pass

        if key_text:
            parts.append(key_text)
            
        final_hotkey = "+".join(parts)
        self.setText(final_hotkey)
        self.current_sequence = final_hotkey
        self.clearFocus()

def window_thumbnail(hwnd, width=48, height=32):
    """Capture a small screenshot of the given window as a QIcon.

    Mirrors how OBS captures windows: the primary method is a GDI ``BitBlt`` of
    the window's own device context (captures the real window, never the
    desktop, with no cropping or overlap). A desktop-DC crop at the window's
    exact rectangle is used only as a fallback for windows where that BitBlt
    comes back blank (e.g. GPU-composited content). Returns None when the window
    can't be captured.
    """
    if not hwnd:
        return None
    # PrintWindow with PW_RENDERFULLCONTENT is the OBS "Windows 10" capture
    # method: it asks the target to paint its full content (including GPU
    # composited clients like Chrome/Vivaldi/Discord) into our DC, so it captures
    # the real window -- never the desktop -- with no cropping or overlap, and
    # works while the window is behind others. This is prioritized over BitBlt,
    # which returns a blank bitmap for most modern windows.
    image = _printwindow_image(hwnd, width, height)
    if _is_good_image(image):
        return QIcon(QPixmap.fromImage(image))
    image = _bitblt_window_image(hwnd, width, height)
    if _is_good_image(image):
        return QIcon(QPixmap.fromImage(image))
    image = _bitblt_desktop_rect(hwnd, width, height)
    if _is_good_image(image):
        return QIcon(QPixmap.fromImage(image))
    return None


def _is_good_image(image):
    """True when ``image`` is a usable, non-blank capture."""
    return image is not None and not image.isNull() and not _is_uniform_image(image)


def _bitblt_window_image(hwnd, width, height):
    """Capture ``hwnd`` by BitBlt-ing its window device context.

    This is the OBS-proven GDI method: ``GetWindowDC`` + ``BitBlt`` of the
    window's own content into a memory bitmap, converted to a ``QImage``. It
    captures the actual window (never the desktop) and works across monitors.

    Returns None on any failure so callers fall back to the desktop-DC crop.
    """
    try:
        import win32con
        import win32gui
        import win32ui
    except Exception:
        return None
    try:
        if win32gui.IsIconic(hwnd):
            rect = win32gui.GetWindowPlacement(hwnd)[4]
        else:
            rect = win32gui.GetWindowRect(hwnd)
        w = max(1, rect[2] - rect[0])
        h = max(1, rect[3] - rect[1])
        hwnd_dc = win32gui.GetWindowDC(hwnd)
        if not hwnd_dc:
            return None
        try:
            mfc_dc = win32ui.CreateDCFromHandle(hwnd_dc)
            save_dc = mfc_dc.CreateCompatibleDC()
            bitmap = win32ui.CreateBitmap()
            bitmap.CreateCompatibleBitmap(mfc_dc, w, h)
            save_dc.SelectObject(bitmap)
            save_dc.BitBlt((0, 0), (w, h), mfc_dc, (0, 0), win32con.SRCCOPY)
            bmp_info = bitmap.GetInfo()
            bmp_str = bitmap.GetBitmapBits(True)
        finally:
            win32gui.ReleaseDC(hwnd, hwnd_dc)
            try:
                mfc_dc.DeleteDC()
            except Exception:
                pass
            try:
                save_dc.DeleteDC()
            except Exception:
                pass
        if not bmp_str:
            return None
        image = QImage(
            bmp_str,
            w,
            h,
            bmp_info["bmWidthBytes"],
            QImage.Format.Format_RGB32,
        )
        if image.isNull():
            return None
        return image.scaled(
            width,
            height,
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )
    except Exception:
        return None


def _printwindow_image(hwnd, width, height):
    """Capture ``hwnd`` with ``PrintWindow`` (PW_RENDERFULLCONTENT).

    This is the technique OBS and Chromium use for windows where a GDI
    ``BitBlt`` hands back a blank bitmap -- GPU-composited clients (Chrome,
    Discord, Vivaldi), occluded, or minimized windows. ``PrintWindow`` asks the
    target to paint its full content (including the client area) into our memory
    DC, so it captures the real window and not the desktop, with no cropping or
    overlap, and works even when the window is behind others.

    pywin32 does not expose ``PrintWindow``, so we call ``user32.PrintWindow``
    directly via ctypes.

    Returns None on any failure.
    """
    hwnd = int(hwnd)
    try:
        import ctypes

        import win32gui
        import win32ui

        user32 = ctypes.windll.user32
        user32.PrintWindow.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_uint]
        user32.PrintWindow.restype = ctypes.c_int
    except Exception:
        return None
    try:
        if win32gui.IsIconic(hwnd):
            rect = win32gui.GetWindowPlacement(hwnd)[4]
        else:
            rect = win32gui.GetWindowRect(hwnd)
        w = max(1, rect[2] - rect[0])
        h = max(1, rect[3] - rect[1])
        hwnd_dc = win32gui.GetWindowDC(hwnd)
        if not hwnd_dc:
            return None
        try:
            mfc_dc = win32ui.CreateDCFromHandle(hwnd_dc)
            save_dc = mfc_dc.CreateCompatibleDC()
            bitmap = win32ui.CreateBitmap()
            bitmap.CreateCompatibleBitmap(mfc_dc, w, h)
            save_dc.SelectObject(bitmap)
            dest = int(save_dc.GetSafeHdc())
            # PW_RENDERFULLCONTENT (2) asks the app to render its full client
            # area; fall back to the plain (0) variant if it is unsupported.
            if not user32.PrintWindow(hwnd, dest, 2) and not user32.PrintWindow(
                hwnd, dest, 0
            ):
                return None
            bmp_info = bitmap.GetInfo()
            bmp_str = bitmap.GetBitmapBits(True)
        finally:
            win32gui.ReleaseDC(hwnd, hwnd_dc)
            try:
                mfc_dc.DeleteDC()
            except Exception:
                pass
            try:
                save_dc.DeleteDC()
            except Exception:
                pass
        if not bmp_str:
            return None
        image = QImage(
            bmp_str,
            w,
            h,
            bmp_info["bmWidthBytes"],
            QImage.Format.Format_RGB32,
        )
        if image.isNull():
            return None
        return image.scaled(
            width,
            height,
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )
    except Exception:
        return None


def _is_uniform_image(image):
    """Return True when ``image`` is blank (a single solid colour).

    PrintWindow frequently hands back an all-black or all-white bitmap for
    GPU-composited windows; such a result should be discarded in favour of a
    screen grab.
    """
    if image is None or image.isNull():
        return True
    try:
        converted = image.convertToFormat(QImage.Format.Format_RGB32)
    except Exception:
        return True
    width = converted.width()
    height = converted.height()
    if width == 0 or height == 0:
        return True
    first = converted.pixelColor(0, 0)
    target = (first.red(), first.green(), first.blue())
    for y in range(height):
        for x in range(width):
            color = converted.pixelColor(x, y)
            if (color.red(), color.green(), color.blue()) != target:
                return False
    return True


def _bitblt_desktop_rect(hwnd, width, height):
    """Fallback capture: BitBlt the desktop DC at the window's exact rect.

    When a direct ``BitBlt`` of the window's own DC comes back blank (GPU
    composited windows, etc.) the desktop device context still holds the
    composited result for the region the window occupies, so sampling that
    region recovers the content. The crop is the window's real rectangle, so it
    is accurate with no overlap or guesswork.

    Returns None on any failure.
    """
    try:
        import win32con
        import win32gui
        import win32ui
    except Exception:
        return None
    try:
        if win32gui.IsIconic(hwnd):
            rect = win32gui.GetWindowPlacement(hwnd)[4]
        else:
            rect = win32gui.GetWindowRect(hwnd)
        left, top = rect[0], rect[1]
        w = max(1, rect[2] - rect[0])
        h = max(1, rect[3] - rect[1])
        desktop_dc = win32gui.GetDC(0)
        if not desktop_dc:
            return None
        try:
            mfc_dc = win32ui.CreateDCFromHandle(desktop_dc)
            save_dc = mfc_dc.CreateCompatibleDC()
            bitmap = win32ui.CreateBitmap()
            bitmap.CreateCompatibleBitmap(mfc_dc, w, h)
            save_dc.SelectObject(bitmap)
            save_dc.BitBlt((0, 0), (w, h), mfc_dc, (left, top), win32con.SRCCOPY)
            bmp_info = bitmap.GetInfo()
            bmp_str = bitmap.GetBitmapBits(True)
        finally:
            win32gui.ReleaseDC(0, desktop_dc)
            try:
                mfc_dc.DeleteDC()
            except Exception:
                pass
            try:
                save_dc.DeleteDC()
            except Exception:
                pass
        if not bmp_str:
            return None
        image = QImage(
            bmp_str,
            w,
            h,
            bmp_info["bmWidthBytes"],
            QImage.Format.Format_RGB32,
        )
        if image.isNull():
            return None
        return image.scaled(
            width,
            height,
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )
    except Exception:
        return None



# source window straight onto our dialog. That removes the per-second
# PrintWindow/BitBlt capture that made the picker laggy. The struct layout
# below mirrors the native DWM_THUMBNAIL_PROPERTIES (DWORD, RECT, RECT, BYTE,
# BOOL, BOOL) so ctypes packs it correctly.
try:
    _dwmapi = ctypes.windll.dwmapi
    _DWM_AVAILABLE = True
except Exception:
    _dwmapi = None
    _DWM_AVAILABLE = False


class _DwmRect(ctypes.Structure):
    _fields_ = [
        ("left", ctypes.c_long),
        ("top", ctypes.c_long),
        ("right", ctypes.c_long),
        ("bottom", ctypes.c_long),
    ]


class _DwmThumbnailProps(ctypes.Structure):
    _fields_ = [
        ("dwFlags", ctypes.c_uint32),
        ("rcDestination", _DwmRect),
        ("rcSource", _DwmRect),
        ("opacity", ctypes.c_byte),
        ("fVisible", ctypes.c_bool),
        ("fSourceClientAreaOnly", ctypes.c_bool),
    ]



class AppPickerDialog(QDialog):
    THUMBNAIL_SIZE = (240, 150)

    def __init__(self, parent=None, recency=None):
        super().__init__(parent)
        self.setWindowTitle("Select Application to Capture")
        self.setMinimumSize(360, 460)
        self.resize(1000, 560)
        self.setWindowIcon(QIcon(resource_path("icon_rec.png")))
        self._recency = recency or {}
        self._loading = True
        self.selected_pid = None
        self.selected_hwnd = None
        self.selected_title = ""
        self.selected_name = ""

        # Incremental state so refreshes never rebuild the whole list.
        self._item_map = {}  # (pid, hwnd) -> QListWidgetItem
        self._thumb_map = {}  # (pid, hwnd) -> DWM thumbnail handle
        self._preview_map = {}  # (pid, hwnd) -> preview QLabel
        self._static_times = {}  # (pid, hwnd) -> last static-capture time

        layout = QVBoxLayout(self)
        self.list = QListWidget()
        self.list.setViewMode(QListWidget.ViewMode.IconMode)
        self.list.setMovement(QListWidget.Movement.Static)
        self.list.setResizeMode(QListWidget.ResizeMode.Adjust)
        self.list.setSpacing(10)
        self.list.setWordWrap(True)
        self.list.setIconSize(QSize(*self.THUMBNAIL_SIZE))
        self.list.itemClicked.connect(self._on_item_clicked)
        layout.addWidget(self.list, 1)

        hint = QLabel("Click a window to capture it. Close this window to cancel.")
        hint.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(hint)

        self._populate()
        self._loading = False

        # Re-enumerate running applications once per second so newly opened or
        # closed windows surface without rebuilding the widget list.
        self._refresh_timer = QTimer(self)
        self._refresh_timer.setInterval(1000)
        self._refresh_timer.timeout.connect(self._populate)
        self._refresh_timer.start()

        # Live DWM thumbnails are cheap to reposition, so refresh their geometry
        # frequently. Static (PrintWindow) fallbacks are throttled separately.
        self._live_timer = QTimer(self)
        self._live_timer.setInterval(100)
        self._live_timer.timeout.connect(self._update_live_thumbnails)
        self._live_timer.start()

        self._static_timer = QTimer(self)
        self._static_timer.setInterval(1500)
        self._static_timer.timeout.connect(self._refresh_static_visible)
        self._static_timer.start()

    def _populate(self):
        try:
            apps = get_active_applications()
        except Exception:
            apps = []
        apps = self._sort_apps(apps)

        current = self.list.currentItem()
        selected_key = current.data(Qt.ItemDataRole.UserRole) if current else None

        seen = set()
        for app in apps:
            key = (app.get("pid"), app.get("hwnd"))
            seen.add(key)
            title = app.get("title", "")
            name = app.get("name", "")
            text = f"{title}\n{name}" if title else name
            item = self._item_map.get(key)
            if item is None:
                item = QListWidgetItem()
                item.setSizeHint(
                    QSize(self.THUMBNAIL_SIZE[0] + 16, self.THUMBNAIL_SIZE[1] + 44)
                )
                item.setData(Qt.ItemDataRole.UserRole, app.get("pid"))
                item.setData(Qt.ItemDataRole.UserRole + 1, app.get("hwnd"))
                self.list.addItem(item)
                widget = self._make_item_widget(key, text)
                self.list.setItemWidget(item, widget)
                self._item_map[key] = item
                self._preview_map[key] = widget.findChild(QLabel, "preview")
                if _DWM_AVAILABLE:
                    self._register_thumb(key, app.get("hwnd"))
            else:
                item.setText(text)
                widget = self.list.itemWidget(item)
                if widget is not None:
                    label = widget.findChild(QLabel, "label")
                    if label is not None:
                        label.setText(text)
            if key == selected_key:
                self.list.setCurrentItem(item)

        # Drop windows that disappeared since the last refresh.
        for key in list(self._item_map):
            if key not in seen:
                item = self._item_map.pop(key)
                self._unregister_thumb(key)
                self._preview_map.pop(key, None)
                self._static_times.pop(key, None)
                row = self.list.row(item)
                if row >= 0:
                    self.list.takeItem(row)

    def _sort_apps(self, apps):
        # The foreground window is the most recently interacted app; boost it so
        # it floats to the top. The picker itself becomes foreground when shown,
        # so ignore our own window handle.
        fg_hwnd, fg_pid = get_foreground_window_info()
        own_hwnd = int(self.winId()) if self.winId() else 0
        if fg_hwnd == own_hwnd:
            fg_pid = None
        for app in apps:
            app["_fg"] = 1 if app.get("pid") == fg_pid else 0

        groups = {}
        for app in apps:
            groups.setdefault(app.get("name", ""), []).append(app)

        # Order each executable group by the most-recently-interacted window it
        # contains (falling back to process creation time). Then interleave the
        # groups round-robin so windows belonging to the same executable are not
        # listed consecutively.
        def group_key(items):
            return (
                max(a["_fg"] for a in items),
                max(self._recency.get(a.get("pid"), 0.0) for a in items),
                max(a.get("create_time", 0.0) for a in items),
            )

        ordered = sorted(groups.values(), key=group_key, reverse=True)
        queues = [list(group) for group in ordered if group]
        result = []
        while queues:
            next_round = []
            for queue in queues:
                result.append(queue.pop(0))
                if queue:
                    next_round.append(queue)
            queues = next_round
        return result

    def _make_item_widget(self, key, text):
        container = QWidget()
        container.setFixedSize(self.THUMBNAIL_SIZE[0] + 16, self.THUMBNAIL_SIZE[1] + 44)
        layout = QVBoxLayout(container)
        layout.setContentsMargins(4, 4, 4, 4)
        preview = QLabel()
        preview.setObjectName("preview")
        preview.setFixedSize(*self.THUMBNAIL_SIZE)
        # Transparent so the OS-composited DWM live thumbnail shows through;
        # when DWM is unavailable we paint a captured pixmap here instead.
        preview.setStyleSheet("background:transparent; border:1px solid #555;")
        preview.setAlignment(Qt.AlignmentFlag.AlignCenter)
        label = QLabel(text)
        label.setObjectName("label")
        label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        label.setWordWrap(True)
        layout.addWidget(preview)
        layout.addWidget(label)

        def _press(event):
            self._select(key)

        container.mousePressEvent = _press
        preview.mousePressEvent = _press
        label.mousePressEvent = _press
        return container

    def _on_item_clicked(self, item):
        if item is None:
            return
        pid = item.data(Qt.ItemDataRole.UserRole)
        hwnd = item.data(Qt.ItemDataRole.UserRole + 1)
        self._select((pid, hwnd))

    def _select(self, key, item=None):
        if self._loading:
            return
        if item is None:
            item = self._item_map.get(key)
        if item is None:
            return
        self.list.setCurrentItem(item)
        app_pid, app_hwnd = key
        self.selected_pid = app_pid
        self.selected_hwnd = app_hwnd
        text = item.text().split("\n")
        self.selected_title = text[0] if len(text) > 1 else ""
        self.selected_name = text[-1]
        self.accept()

    # ── Live DWM thumbnails ──────────────────────────────────────────────────

    def _register_thumb(self, key, hwnd):
        if hwnd is None or not _DWM_AVAILABLE:
            return
        try:
            handle = ctypes.c_void_p()
            hr = _dwmapi.DwmRegisterThumbnail(
                ctypes.c_void_p(int(self.winId())),
                ctypes.c_void_p(int(hwnd)),
                ctypes.byref(handle),
            )
            if hr == 0 and handle.value:
                self._thumb_map[key] = handle.value
        except Exception:
            pass

    def _unregister_thumb(self, key):
        handle = self._thumb_map.pop(key, None)
        if handle is None or not _DWM_AVAILABLE:
            return
        try:
            _dwmapi.DwmUnregisterThumbnail(ctypes.c_void_p(handle))
        except Exception:
            pass

    def _update_live_thumbnails(self):
        if not _DWM_AVAILABLE:
            return
        for key, item in list(self._item_map.items()):
            preview = self._preview_map.get(key)
            if preview is None or not preview.isVisible():
                continue
            handle = self._thumb_map.get(key)
            if handle is None:
                self._register_thumb(key, key[1])
                handle = self._thumb_map.get(key)
            if handle is None:
                self._static_fallback(key, preview)
                continue
            top = preview.mapTo(self, QPoint(0, 0))
            props = _DwmThumbnailProps()
            props.dwFlags = (
                _DWM_TNP_RECTDESTINATION
                | _DWM_TNP_VISIBLE
                | _DWM_TNP_SOURCECLIENTAREAONLY
                | _DWM_TNP_OPACITY
            )
            props.rcDestination.left = top.x()
            props.rcDestination.top = top.y()
            props.rcDestination.right = top.x() + preview.width()
            props.rcDestination.bottom = top.y() + preview.height()
            props.opacity = 255
            props.fVisible = True
            props.fSourceClientAreaOnly = True
            try:
                _dwmapi.DwmUpdateThumbnailProperties(
                    ctypes.c_void_p(handle), ctypes.byref(props)
                )
            except Exception:
                self._unregister_thumb(key)
                self._static_fallback(key, preview)

    def _refresh_static_visible(self):
        for key, item in list(self._item_map.items()):
            if key in self._thumb_map:
                continue
            preview = self._preview_map.get(key)
            if preview is None or not preview.isVisible():
                continue
            self._static_fallback(key, preview)

    def _static_fallback(self, key, preview):
        now = time.time()
        last = self._static_times.get(key, 0.0)
        if now - last < 1.5:
            return
        self._static_times[key] = now
        icon = window_thumbnail(key[1], *self.THUMBNAIL_SIZE)
        if icon is not None:
            preview.setPixmap(icon.pixmap(*self.THUMBNAIL_SIZE))

    def _cleanup(self):
        self._refresh_timer.stop()
        self._live_timer.stop()
        self._static_timer.stop()
        for key in list(self._thumb_map):
            self._unregister_thumb(key)

    def accept(self):
        self._cleanup()
        super().accept()

    def reject(self):
        self._cleanup()
        super().reject()

    def closeEvent(self, event):
        self._cleanup()
        super().closeEvent(event)




class TrackEditor(QWidget):
    """One row of the track list: which source this track captures."""

    KIND_INPUT = "Input Device"
    KIND_OUTPUT = "Output (System Audio)"
    KIND_APP = "Application"

    removed = pyqtSignal(object)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._loading = False

        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        self.kind_combo = QComboBox()
        self.kind_combo.addItems([self.KIND_INPUT, self.KIND_OUTPUT, self.KIND_APP])
        self.kind_combo.currentIndexChanged.connect(self.on_kind_changed)
        layout.addWidget(self.kind_combo, 2)

        self.device_combo = QComboBox()
        layout.addWidget(self.device_combo, 3)

        self.app_btn = QPushButton("Choose App...")
        self.app_btn.setToolTip("Capture one application's audio")
        self.app_btn.clicked.connect(self.open_app_picker)
        layout.addWidget(self.app_btn)

        btn_remove = QPushButton("Remove")
        btn_remove.clicked.connect(lambda: self.removed.emit(self))
        layout.addWidget(btn_remove)

    def current_kind(self):
        text = self.kind_combo.currentText()
        if text == self.KIND_OUTPUT:
            return "output"
        if text == self.KIND_APP:
            return "application"
        return "input"

    def on_kind_changed(self, _index=None):
        if not self._loading:
            self.refresh_devices()

    def refresh_devices(self):
        """Fills the device list for the selected kind."""
        self.app_btn.setEnabled(self.current_kind() == "application")
        self._loading = True
        try:
            self.device_combo.clear()
            kind = self.current_kind()
            if kind == "application":
                # Applications come and go, so this list is always fetched fresh.
                for app in get_active_applications():
                    self.device_combo.addItem(self.app_text(app["title"], app["name"]), app["pid"])
                return
            if kind == "input":
                for device in get_devices(include_loopback=False):
                    self.device_combo.addItem(device["name"], device["id"])
                default_id = sc.default_microphone().id
            else:
                for speaker in get_speakers():
                    self.device_combo.addItem(speaker["name"], speaker["id"])
                default_id = sc.default_speaker().id
            index = self.device_combo.findData(default_id)
            if index >= 0:
                self.device_combo.setCurrentIndex(index)
        except Exception as e:
            print(f"Error refreshing devices: {e}")
        finally:
            self._loading = False

    @staticmethod
    def app_text(title, name):
        title, name = (title or "").strip(), (name or "").strip()
        if title and name:
            return f"{title} ({name})"
        return title or name or "Unknown application"

    def open_app_picker(self):
        """Opens the application picker and adopts the chosen application."""
        recency = getattr(self.parent(), "_app_recency", {}) or {}
        dialog = AppPickerDialog(self, recency=recency)
        if dialog.exec() == QDialog.DialogCode.Accepted and dialog.selected_pid is not None:
            self.set_app(dialog.selected_pid, dialog.selected_hwnd,
                         dialog.selected_title, dialog.selected_name)

    def set_app(self, pid, hwnd, title, name):
        """Records the picked application and shows it on this row."""
        if self.kind_combo.currentText() != self.KIND_APP:
            self._loading = True
            self.kind_combo.setCurrentText(self.KIND_APP)
            self._loading = False
            self.refresh_devices()
        label = self.app_text(title, name)
        index = self.device_combo.findData(pid)
        if index < 0:
            self.device_combo.addItem(label, pid)
            index = self.device_combo.count() - 1
        else:
            self.device_combo.setItemText(index, label)
        self.device_combo.setCurrentIndex(index)

    def get_track(self):
        kind = self.current_kind()
        if kind == "input":
            return {"kind": "input", "input_id": self.device_combo.currentData()}
        if kind == "output":
            return {"kind": "output", "speaker_id": self.device_combo.currentData()}
        return {"kind": "application",
                "target_pid": self.device_combo.currentData(),
                "target_name": self.device_combo.currentText()}

    def set_track(self, track):
        kind = str(track.get("kind") or "input").strip().lower()
        label = {"output": self.KIND_OUTPUT, "application": self.KIND_APP}.get(kind, self.KIND_INPUT)
        self._loading = True
        self.kind_combo.setCurrentText(label)
        self._loading = False
        self.refresh_devices()
        if kind == "output":
            wanted = track.get("speaker_id")
        elif kind == "application":
            wanted = track.get("target_pid")
        else:
            wanted = track.get("input_id")
        if wanted is not None:
            index = self.device_combo.findData(wanted)
            if index >= 0:
                self.device_combo.setCurrentIndex(index)


class SettingsWindow(QMainWindow):
    settings_saved = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Settings - Simple Audio Recorder")
        self.setGeometry(100, 100, 500, 600)
        
        self.init_ui()
        self.load_settings()
        if not self.track_editors():
            self.add_track()

    def init_ui(self):
        layout = QVBoxLayout()
        container = QWidget()
        container.setLayout(layout)
        self.setCentralWidget(container)

        # Tracks captured simultaneously
        group_tracks = QGroupBox("Tracks")
        layout_tracks = QVBoxLayout()
        self.tracks_area = QScrollArea()
        self.tracks_area.setWidgetResizable(True)
        self.tracks_area.setMinimumHeight(110)
        self.tracks_container = QWidget()
        self.tracks_layout = QVBoxLayout(self.tracks_container)
        self.tracks_layout.addStretch(1)
        self.tracks_area.setWidget(self.tracks_container)
        layout_tracks.addWidget(self.tracks_area)
        btn_add_track = QPushButton("Add Track")
        btn_add_track.clicked.connect(lambda: self.add_track())
        layout_tracks.addWidget(btn_add_track)
        btn_refresh = QPushButton("Refresh Devices")
        btn_refresh.clicked.connect(self.refresh_devices)
        layout_tracks.addWidget(btn_refresh)
        group_tracks.setLayout(layout_tracks)
        layout.addWidget(group_tracks)

        # Output
        group_out = QGroupBox("Output Configuration")
        layout_out = QFormLayout()
        
        layout_folder_inner = QHBoxLayout()
        self.lbl_folder = QLabel(os.getcwd())
        btn_browse = QPushButton("Browse...")
        btn_browse.clicked.connect(self.browse_folder)
        layout_folder_inner.addWidget(self.lbl_folder)
        layout_folder_inner.addWidget(btn_browse)
        
        self.combo_fmt = QComboBox()
        self.combo_fmt.addItems(["MP3", "WAV", "FLAC"])
        self.chk_mono = QCheckBox("Save as mono (half the size, good for voice)")
        self.combo_output_mode = QComboBox()
        self.combo_output_mode.addItem("Mixed (one file)", "mixed")
        self.combo_output_mode.addItem("Separate files", "separate_files")
        
        layout_out.addRow("Folder:", layout_folder_inner)
        layout_out.addRow("Format:", self.combo_fmt)
        layout_out.addRow("Output mode:", self.combo_output_mode)
        layout_out.addRow("", self.chk_mono)
        group_out.setLayout(layout_out)
        layout.addWidget(group_out)

        # Tray Interaction
        group_tray = QGroupBox("Tray Icon Behavior")
        layout_tray = QFormLayout()
        self.spin_auto_stop = QSpinBox()
        self.spin_auto_stop.setRange(0, 24 * 60)
        self.spin_auto_stop.setSuffix(" min")
        self.spin_auto_stop.setSpecialValueText("Off")
        layout_tray.addRow("Stop Automatically After:", self.spin_auto_stop)
        self.chk_notifications = QCheckBox("Show notifications (errors are always shown)")
        self.chk_notifications.setChecked(True)
        layout_tray.addRow(self.chk_notifications)
        group_tray.setLayout(layout_tray)
        layout.addWidget(group_tray)

        # Post-Processing
        group_post = QGroupBox("Post-Processing & Clipboard")
        layout_post = QVBoxLayout()
        self.chk_normalize = QCheckBox("Normalize Audio (Apply first)")
        self.chk_clipboard = QCheckBox("Copy File to Clipboard")
        self.chk_delete = QCheckBox("Delete after Copy (Move to Temp)")
        self.chk_delete.setToolTip("Moves the file to the system temp folder before copying, keeping your output folder clean.")
        self.chk_delete.setEnabled(False)
        self.chk_clipboard.toggled.connect(lambda c: self.chk_delete.setEnabled(c))
        
        layout_post.addWidget(self.chk_normalize)
        layout_post.addWidget(self.chk_clipboard)
        layout_post.addWidget(self.chk_delete)
        group_post.setLayout(layout_post)
        layout.addWidget(group_post)

        # Hotkeys
        group_hotkeys = QGroupBox("Global Hotkeys")
        layout_hotkeys = QFormLayout()
        self.hk_record = HotkeyEdit()
        self.hk_toggle = HotkeyEdit()
        self.hk_stop = HotkeyEdit()
        layout_hotkeys.addRow("Start Recording:", self.hk_record)
        layout_hotkeys.addRow("Stop Recording:", self.hk_stop)
        layout_hotkeys.addRow("Toggle Recording:", self.hk_toggle)
        group_hotkeys.setLayout(layout_hotkeys)
        layout.addWidget(group_hotkeys)

        btn_save = QPushButton("Save Settings")
        btn_save.clicked.connect(self.save_settings)
        layout.addWidget(btn_save)

        self.refresh_devices()

    def refresh_devices(self):
        for editor in self.track_editors():
            editor.refresh_devices()

    def add_track(self, track=None):
        """Appends a track row, optionally seeded from a saved track dict."""
        editor = TrackEditor()
        editor.removed.connect(self.remove_track)
        self.tracks_layout.insertWidget(self.tracks_layout.count() - 1, editor)
        editor.refresh_devices()
        if track:
            editor.set_track(track)
        return editor

    def remove_track(self, editor):
        if len(self.track_editors()) <= 1:
            QMessageBox.information(self, "Tracks", "At least one track is required.")
            return
        self.tracks_layout.removeWidget(editor)
        editor.setParent(None)
        editor.deleteLater()

    def track_editors(self):
        """The live track rows, in display order."""
        editors = []
        for i in range(self.tracks_layout.count()):
            widget = self.tracks_layout.itemAt(i).widget()
            if isinstance(widget, TrackEditor):
                editors.append(widget)
        return editors

    def clear_tracks(self):
        for editor in self.track_editors():
            self.tracks_layout.removeWidget(editor)
            editor.setParent(None)
            editor.deleteLater()

    def get_tracks(self):
        return [editor.get_track() for editor in self.track_editors()]

    def browse_folder(self):
        folder = QFileDialog.getExistingDirectory(self, "Select Output Folder", options=QFileDialog.Option.DontUseNativeDialog)
        if folder:
            self.lbl_folder.setText(folder)

    def load_settings(self):
        if os.path.exists(CONFIG_FILE):
            try:
                with open(CONFIG_FILE, 'r') as f:
                    data = json.load(f)
                    
                self.lbl_folder.setText(data.get("output_folder", os.getcwd()))
                fmt_idx = self.combo_fmt.findText(data.get("format", "MP3"))
                if fmt_idx >= 0: self.combo_fmt.setCurrentIndex(fmt_idx)
                
                self.clear_tracks()
                for track in data.get("tracks") or []:
                    self.add_track(track)
                if not self.track_editors():
                    self.add_track({"kind": "input", "input_id": data.get("device_id")})
                mode_idx = self.combo_output_mode.findData(data.get("output_mode", "mixed"))
                if mode_idx >= 0: self.combo_output_mode.setCurrentIndex(mode_idx)

                self.spin_auto_stop.setValue(data.get("auto_stop_min", 0))
                self.chk_notifications.setChecked(data.get("notifications", True))

                self.chk_mono.setChecked(data.get("mono", False))
                self.chk_normalize.setChecked(data.get("normalize", False))
                self.chk_clipboard.setChecked(data.get("clipboard", False))
                self.chk_delete.setChecked(data.get("delete_after", False))
                self.chk_delete.setEnabled(self.chk_clipboard.isChecked())

                # an older settings file could hold three per-mode hotkeys;
                # keep the most useful one as the single record hotkey
                self.hk_record.setText(data.get("hk_record",
                                               data.get("hk_both") or data.get("hk_mic") or ""))
                self.hk_toggle.setText(data.get("hk_toggle", ""))
                self.hk_stop.setText(data.get("hk_stop", ""))
            except Exception as e:
                print(f"Error loading settings: {e}")

    def save_settings(self):
        data = self.get_settings()
        try:
            with open(CONFIG_FILE, 'w') as f:
                json.dump(data, f)
            QMessageBox.information(self, "Settings", "Settings saved successfully.")
            self.settings_saved.emit()
        except Exception as e:
            QMessageBox.critical(self, "Error", f"Failed to save settings: {e}")

    def get_settings(self):
        tracks = self.get_tracks()
        first_input = next((t.get("input_id") for t in tracks if t.get("kind") == "input"), None)
        return {
            "device_id": first_input,
            "tracks": tracks,
            "output_mode": self.combo_output_mode.currentData(),
            "output_folder": self.lbl_folder.text(),
            "format": self.combo_fmt.currentText(),
            "mono": self.chk_mono.isChecked(),
            "auto_stop_min": self.spin_auto_stop.value(),
            "notifications": self.chk_notifications.isChecked(),
            "normalize": self.chk_normalize.isChecked(),
            "clipboard": self.chk_clipboard.isChecked(),
            "delete_after": self.chk_delete.isChecked(),
            "hk_record": self.hk_record.text(),
            "hk_toggle": self.hk_toggle.text(),
            "hk_stop": self.hk_stop.text()
        }

class TrayApplication(QObject):
    def __init__(self, app):
        super().__init__()
        self.app = app
        self.recorder = None
        
        self.signals = SignalManager()
        self.signals.recording_finished.connect(self.on_recording_finished)
        self.signals.saving.connect(self.on_saving)
        self.signals.progress.connect(self.on_save_progress)
        self.save_percent = -1
        self.signals.hotkey.connect(self.on_hotkey)
        self.signals.recording_notice.connect(self.on_recording_notice)

        # ticks while recording: elapsed time in the tooltip, auto stop
        self.rec_timer = QTimer(self)
        self.rec_timer.setInterval(1000)
        self.rec_timer.timeout.connect(self.on_recording_tick)
        self.rec_started = 0

        self.icon_idle_path = resource_path("icon_idle.png")
        self.icon_rec_path = resource_path("icon_rec.png")
        self.generate_icons()
            
        self.tray_icon = QSystemTrayIcon(QIcon(self.icon_idle_path), self.app)
        self.tray_icon.setToolTip("Simple Audio Recorder (Idle)")
        self.tray_icon.activated.connect(self.on_tray_activated)
        
        self.build_menu()
        self.tray_icon.show()
        
        self.settings_window = SettingsWindow()
        self.settings_window.settings_saved.connect(self.register_hotkeys)
        
        self.notify("Ready", "Left-click to toggle recording.", QSystemTrayIcon.MessageIcon.Information, 2000)
        self.register_hotkeys()

    def generate_icons(self):
        if not os.path.exists(self.icon_idle_path):
            pix = QPixmap(64, 64)
            pix.fill(Qt.GlobalColor.transparent)
            painter = QPainter(pix)
            painter.setRenderHint(QPainter.RenderHint.Antialiasing)
            painter.setBrush(QBrush(QColor(80, 80, 80)))
            painter.setPen(Qt.PenStyle.NoPen)
            painter.drawEllipse(4, 4, 56, 56)
            painter.end()
            pix.save(self.icon_idle_path)

        if not os.path.exists(self.icon_rec_path):
            pix = QPixmap(64, 64)
            pix.fill(Qt.GlobalColor.transparent)
            painter = QPainter(pix)
            painter.setRenderHint(QPainter.RenderHint.Antialiasing)
            painter.setBrush(QBrush(QColor(220, 0, 0)))
            painter.setPen(Qt.PenStyle.NoPen)
            painter.drawEllipse(4, 4, 56, 56)
            painter.setBrush(QBrush(QColor(255, 255, 255)))
            painter.drawEllipse(22, 22, 20, 20)
            painter.end()
            pix.save(self.icon_rec_path)

    def build_menu(self):
        self.menu = QMenu()
        # One way in: the track list in Settings decides what gets captured.
        self.action_record = QAction("Start Recording", self)
        self.action_record.triggered.connect(lambda: self.start_recording())
        self.action_stop = QAction("Stop Recording", self)
        self.action_stop.triggered.connect(self.stop_recording)
        self.action_stop.setEnabled(False)
        self.action_settings = QAction("Settings", self)
        self.action_settings.triggered.connect(self.open_settings)
        self.action_exit = QAction("Exit", self)
        self.action_exit.triggered.connect(self.exit_app)
        
        self.menu.addAction(self.action_record)
        self.menu.addAction(self.action_stop)
        self.menu.addSeparator()
        self.menu.addAction(self.action_settings)
        self.menu.addAction(self.action_exit)
        self.tray_icon.setContextMenu(self.menu)

    def register_hotkeys(self):
        try: keyboard.unhook_all_hotkeys() # Ensure no old hotkeys are active
        except: pass
        settings = self.settings_window.get_settings()
        hk_record = settings.get("hk_record")
        hk_toggle = settings.get("hk_toggle")
        hk_stop = settings.get("hk_stop")
        try:
            # keyboard runs callbacks on its own thread, hand them to the Qt thread
            if hk_record: keyboard.add_hotkey(hk_record, lambda: self.signals.hotkey.emit("record"))
            if hk_toggle: keyboard.add_hotkey(hk_toggle, lambda: self.signals.hotkey.emit("toggle"))
            if hk_stop: keyboard.add_hotkey(hk_stop, lambda: self.signals.hotkey.emit("stop"))
        except Exception as e: print(f"Failed to register hotkeys: {e}")

    def on_hotkey(self, action):
        # "toggle" flips recording, "record" only starts, "stop" only stops
        recording = bool(self.recorder and self.recorder.is_alive())
        if action == "stop" or (action == "toggle" and recording):
            self.stop_recording()
        elif action in ("record", "toggle"):
            self.start_recording()

    def notify(self, title, msg, icon=QSystemTrayIcon.MessageIcon.Information, ms=2000):
        if self.settings_window.chk_notifications.isChecked():
            self.tray_icon.showMessage(title, msg, icon, ms)

    def on_tray_activated(self, reason):
        if reason == QSystemTrayIcon.ActivationReason.Trigger:
            if self.recorder and self.recorder.is_alive():
                self.stop_recording()
            else:
                # Left click toggles recording with the configured tracks.
                self.start_recording()

    def open_settings(self):
        self.settings_window.show()
        self.settings_window.raise_()
        self.settings_window.activateWindow()

    def start_recording(self, mode="tracks"):
        if self.recorder and self.recorder.is_alive(): return
        settings = self.settings_window.get_settings()
        target_id = settings['device_id']
        use_tracks = mode == "tracks"
        
        def finish_callback(path, error):
            self.signals.recording_finished.emit(path if path else "", error if error else "")

        # "tracks" records the configured track list; the older mic/loopback/
        # both modes stay available as one-track shortcuts.
        source = ({"tracks": settings.get("tracks")} if use_tracks
                  else {"mic_id": target_id, "source_mode": mode})
        self.recorder = AudioRecorder(
            output_folder=settings['output_folder'],
            output_format=settings['format'],
            normalize=settings['normalize'],
            mono=settings['mono'],
            output_mode=settings.get('output_mode', 'mixed'),
            on_finish_callback=finish_callback,
            on_progress=self.signals.progress.emit,
            on_notice=self.signals.recording_notice.emit,
            **source
        )
        self.recorder.start()
        self.action_record.setEnabled(False)
        self.action_stop.setEnabled(True)
        self.tray_icon.setIcon(QIcon(self.icon_rec_path)) 
        self.tray_icon.setToolTip(f"Recording ({mode}) 00:00")
        self.rec_started = time.monotonic()
        self.rec_timer.start()
        label = "%d tracks" % len(settings.get("tracks") or []) if use_tracks else mode
        self.notify("Started", f"Recording {label}", QSystemTrayIcon.MessageIcon.NoIcon, 1000)

    def on_recording_tick(self):
        elapsed = int(time.monotonic() - self.rec_started)
        minutes, seconds = divmod(elapsed, 60)
        self.tray_icon.setToolTip(f"Recording {minutes:02d}:{seconds:02d}")
        limit = self.settings_window.get_settings()["auto_stop_min"]
        if limit and elapsed >= limit * 60:
            self.stop_recording()

    def stop_recording(self):
        if self.recorder and not self.recorder.stop_event.is_set():
            self.recorder.stop()
            # hotkeys call this from the keyboard thread, so update the UI via a signal
            self.signals.saving.emit()

    def on_saving(self):
        self.rec_timer.stop()
        # Encoding a long recording takes a while, show right away that the click worked
        self.action_stop.setEnabled(False)
        self.save_percent = -1
        self.on_save_progress(0.0)

    def on_save_progress(self, fraction):
        percent = int(fraction * 100)
        if percent == self.save_percent:
            return
        self.save_percent = percent
        self.tray_icon.setIcon(self.saving_icon(percent))
        self.tray_icon.setToolTip(f"Saving recording... {percent}%")
        self.action_stop.setText(f"Saving... {percent}%")

    @staticmethod
    def saving_icon(percent):
        # dim orange disc that fills up bright orange clockwise from the top,
        # orange from the start so it never looks like the idle icon
        pix = QPixmap(64, 64)
        pix.fill(Qt.GlobalColor.transparent)
        painter = QPainter(pix)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QBrush(QColor(120, 70, 10)))
        painter.drawEllipse(4, 4, 56, 56)
        painter.setBrush(QBrush(QColor(255, 150, 0)))
        painter.drawPie(4, 4, 56, 56, 90 * 16, -int(360 * 16 * percent / 100))
        painter.setBrush(QBrush(QColor(255, 255, 255)))
        painter.drawEllipse(24, 24, 16, 16)
        painter.end()
        return QIcon(pix)

    def on_recording_notice(self, message):
        # the recorder stopped itself, shown even with notifications off
        self.tray_icon.showMessage("Recording", message, QSystemTrayIcon.MessageIcon.Warning, 3000)
        self.on_saving()

    def on_recording_finished(self, path, error):
        self.rec_timer.stop()
        self.action_record.setEnabled(True)
        self.action_stop.setEnabled(False)
        self.action_stop.setText("Stop Recording")
        self.tray_icon.setIcon(QIcon(self.icon_idle_path))
        self.tray_icon.setToolTip("Simple Audio Recorder (Idle)")
        self.recorder = None

        # separate-track mode finishes with a list; mixed mode with one path
        paths = [p for p in (path if isinstance(path, (list, tuple)) else [path]) if p]

        # a partial recording comes back with a path and an error, keep it and warn
        if error and not any(os.path.exists(p) for p in paths):
            self.tray_icon.showMessage("Error", f"Recording failed: {error}", QSystemTrayIcon.MessageIcon.Critical, 4000)
            return

        settings = self.settings_window.get_settings()
        if len(paths) == 1:
            msg = f"Saved to {os.path.basename(paths[0])}"
        else:
            msg = "Saved %d tracks:\n%s" % (len(paths), "\n".join(os.path.basename(p) for p in paths))
        if error:
            msg += f"\nWarning: {error}"

        keepers = [p for p in paths if os.path.exists(p)]
        if settings['clipboard'] and keepers:
            try:
                if settings['delete_after']:
                    temp_dir = tempfile.gettempdir()
                    moved = []
                    for p in keepers:
                        new_path = os.path.join(temp_dir, os.path.basename(p))
                        if os.path.exists(new_path):
                            base, ext = os.path.splitext(new_path)
                            new_path = f"{base}_{int(time.time())}{ext}"
                        shutil.move(p, new_path)
                        moved.append(new_path)
                    keepers = moved
                    msg = "Moved to Temp & Copied to Clipboard."

                for p in keepers:
                    success, status = copy_file_to_clipboard(p)
                    if not success:
                        msg += f"\nClipboard Error: {status}"
                if not settings['delete_after']:
                    msg += "\nCopied to clipboard."
            except Exception as e:
                msg += f"\nClipboard/Move error: {e}"

        self.notify("Finished", msg, QSystemTrayIcon.MessageIcon.Information, 2000)

    def exit_app(self):
        if self.recorder: self.recorder.stop()
        self.app.quit()
