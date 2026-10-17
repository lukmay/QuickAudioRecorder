import sys
import os
import json
import shutil
import tempfile
import time
from PyQt6.QtWidgets import (QApplication, QSystemTrayIcon, QMenu, QMainWindow, 
                             QWidget, QVBoxLayout, QHBoxLayout, QLabel, QComboBox, 
                             QPushButton, QFileDialog, QMessageBox, QGroupBox, 
                             QLineEdit, QFormLayout, QCheckBox, QSpinBox, QScrollArea)
from PyQt6.QtGui import QIcon, QAction, QColor, QPixmap, QPainter, QBrush, QKeySequence
from PyQt6.QtCore import pyqtSignal, QObject, Qt, QUrl, QMimeData, QDir, QEvent, QTimer
import soundcard as sc
import keyboard
from audio_recorder import AudioRecorder, get_devices, get_speakers
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

class TrackEditor(QWidget):
    """One row of the track list: which source this track captures."""

    KIND_INPUT = "Input Device"
    KIND_OUTPUT = "Output (System Audio)"

    removed = pyqtSignal(object)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._loading = False

        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        self.kind_combo = QComboBox()
        self.kind_combo.addItems([self.KIND_INPUT, self.KIND_OUTPUT])
        self.kind_combo.currentIndexChanged.connect(self.on_kind_changed)
        layout.addWidget(self.kind_combo, 2)

        self.device_combo = QComboBox()
        layout.addWidget(self.device_combo, 3)

        btn_remove = QPushButton("Remove")
        btn_remove.clicked.connect(lambda: self.removed.emit(self))
        layout.addWidget(btn_remove)

    def current_kind(self):
        return "output" if self.kind_combo.currentText() == self.KIND_OUTPUT else "input"

    def on_kind_changed(self, _index=None):
        if not self._loading:
            self.refresh_devices()

    def refresh_devices(self):
        """Fills the device list for the selected kind."""
        self._loading = True
        try:
            self.device_combo.clear()
            if self.current_kind() == "input":
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

    def get_track(self):
        if self.current_kind() == "input":
            return {"kind": "input", "input_id": self.device_combo.currentData()}
        return {"kind": "output", "speaker_id": self.device_combo.currentData()}

    def set_track(self, track):
        kind = str(track.get("kind") or "input").strip().lower()
        self._loading = True
        self.kind_combo.setCurrentText(self.KIND_OUTPUT if kind == "output" else self.KIND_INPUT)
        self._loading = False
        self.refresh_devices()
        wanted = track.get("speaker_id") if kind == "output" else track.get("input_id")
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
        self.hk_mic = HotkeyEdit()
        self.hk_loop = HotkeyEdit()
        self.hk_both = HotkeyEdit()
        self.hk_stop = HotkeyEdit()
        layout_hotkeys.addRow("Record Mic:", self.hk_mic)
        layout_hotkeys.addRow("Record Loopback:", self.hk_loop)
        layout_hotkeys.addRow("Record Both:", self.hk_both)
        layout_hotkeys.addRow("Stop Recording:", self.hk_stop)
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

                self.hk_mic.setText(data.get("hk_mic", ""))
                self.hk_loop.setText(data.get("hk_loop", ""))
                self.hk_both.setText(data.get("hk_both", ""))
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
            "hk_mic": self.hk_mic.text(),
            "hk_loop": self.hk_loop.text(),
            "hk_both": self.hk_both.text(),
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
        hk_mic = settings.get("hk_mic")
        hk_loop = settings.get("hk_loop")
        hk_both = settings.get("hk_both")
        hk_stop = settings.get("hk_stop")
        try:
            # keyboard runs callbacks on its own thread, hand them to the Qt thread
            if hk_mic: keyboard.add_hotkey(hk_mic, lambda: self.signals.hotkey.emit("mic"))
            if hk_loop: keyboard.add_hotkey(hk_loop, lambda: self.signals.hotkey.emit("loopback"))
            if hk_both: keyboard.add_hotkey(hk_both, lambda: self.signals.hotkey.emit("both"))
            if hk_stop: keyboard.add_hotkey(hk_stop, lambda: self.signals.hotkey.emit("stop"))
        except Exception as e: print(f"Failed to register hotkeys: {e}")

    def on_hotkey(self, mode):
        # a record hotkey pressed again stops the recording
        if mode == "stop" or (self.recorder and self.recorder.is_alive()):
            self.stop_recording()
        else:
            self.start_recording(mode)

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
