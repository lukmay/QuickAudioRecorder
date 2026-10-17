"""Hotkey behaviour: one record hotkey, one toggle hotkey, one stop."""
import os
import time

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtWidgets import QApplication

import gui


@pytest.fixture
def tray(fake_sc, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(gui, "sc", fake_sc)
    monkeypatch.setattr(gui, "get_devices",
                        lambda include_loopback=False: [{"id": "Fake Mic", "name": "Fake Mic"}])
    app = QApplication.instance() or QApplication([])
    t = gui.TrayApplication(app)
    t._test_app = app
    t.settings_window.lbl_folder.setText(str(tmp_path / "out"))
    t.settings_window.combo_fmt.setCurrentText("WAV")
    yield t
    if t.recorder:
        t.recorder.stop()
        t.recorder.join(10)


def wait_for(cond, timeout=15):
    app = QApplication.instance()
    end = time.time() + timeout
    while time.time() < end:
        app.processEvents()
        if cond():
            return True
        time.sleep(0.02)
    return False


def test_the_record_hotkey_starts_and_keeps_recording(tray):
    tray.on_hotkey("record")
    assert wait_for(lambda: tray.recorder is not None and tray.recorder.is_alive())

    tray.on_hotkey("record")  # plain record must not stop it
    assert tray.recorder is not None and tray.recorder.is_alive()


def test_the_toggle_hotkey_starts_then_stops(tray):
    tray.on_hotkey("toggle")
    assert wait_for(lambda: tray.recorder is not None and tray.recorder.is_alive())

    tray.on_hotkey("toggle")
    assert wait_for(lambda: tray.recorder is None)


def test_the_stop_hotkey_stops_a_recording_and_is_harmless_when_idle(tray):
    tray.on_hotkey("stop")
    assert tray.recorder is None

    tray.on_hotkey("record")
    assert wait_for(lambda: tray.recorder is not None and tray.recorder.is_alive())

    tray.on_hotkey("stop")
    assert wait_for(lambda: tray.recorder is None)
