"""Track list UI, per-track source selection, and the output mode setting."""
import json
import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtWidgets import QApplication

import gui


@pytest.fixture
def window(fake_sc, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(gui, "sc", fake_sc)
    monkeypatch.setattr(gui, "get_devices",
                        lambda include_loopback=False: [{"id": "Fake Mic", "name": "Fake Mic"}])
    monkeypatch.setattr(gui, "get_speakers",
                        lambda: [{"id": "spk", "name": "Fake Speakers"}])
    monkeypatch.setattr(gui.QMessageBox, "information", lambda *a, **k: None)
    app = QApplication.instance() or QApplication([])
    w = gui.SettingsWindow()
    w._test_app = app  # keep the QApplication alive for the test
    w.lbl_folder.setText(str(tmp_path))
    return w


def select_mode(window, mode):
    index = window.combo_output_mode.findData(mode)
    assert index >= 0, "output mode %r is not offered" % mode
    window.combo_output_mode.setCurrentIndex(index)


def test_the_window_starts_with_one_input_track(window):
    assert len(window.track_editors()) == 1
    assert window.get_tracks() == [{"kind": "input", "input_id": "Fake Mic"}]


def test_tracks_can_be_added_and_removed(window):
    window.add_track()
    assert len(window.track_editors()) == 2
    window.remove_track(window.track_editors()[-1])
    assert len(window.track_editors()) == 1


def test_the_last_track_cannot_be_removed(window):
    window.remove_track(window.track_editors()[0])
    assert len(window.track_editors()) == 1


def test_a_track_picks_its_own_source(window):
    editor = window.add_track()

    editor.kind_combo.setCurrentText(gui.TrackEditor.KIND_OUTPUT)
    editor.refresh_devices()
    assert editor.get_track() == {"kind": "output", "speaker_id": "spk"}

    editor.kind_combo.setCurrentText(gui.TrackEditor.KIND_INPUT)
    editor.refresh_devices()
    assert editor.get_track() == {"kind": "input", "input_id": "Fake Mic"}


def test_unavailable_endpoint_id_survives_settings_round_trip(window, tmp_path):
    endpoint_id = "{0.0.1.00000000}.{missing-endpoint}"
    with open(tmp_path / gui.CONFIG_FILE, "w") as f:
        json.dump({"device_id": endpoint_id}, f)
    window.load_settings()
    assert window.get_tracks()[0]["input_id"] == endpoint_id
    assert "Unavailable" in window.track_editors()[0].device_combo.currentText()
    with open(tmp_path / gui.CONFIG_FILE, "w") as f:
        json.dump(window.get_settings(), f)
    window.load_settings()
    assert window.get_tracks()[0]["input_id"] == endpoint_id
    editor = window.track_editors()[0]
    editor.device_combo.setCurrentIndex(editor.device_combo.findData("Fake Mic"))
    assert window.get_tracks()[0]["input_id"] == "Fake Mic"


def test_default_device_does_not_have_to_be_first(window, monkeypatch):
    monkeypatch.setattr(gui, "get_devices", lambda **kwargs: [
        {"id": "other", "name": "Other"},
        {"id": "Fake Mic", "name": "Default"},
    ])
    editor = window.track_editors()[0]
    editor.refresh_devices()
    assert editor.get_track()["input_id"] == "Fake Mic"


@pytest.mark.parametrize("kind,key,value", [
    ("input", "input_id", "missing-mic"),
    ("output", "speaker_id", "missing-speaker"),
    ("application", "target_pid", 987654),
])
def test_refresh_preserves_selected_source(window, monkeypatch, kind, key, value):
    monkeypatch.setattr(gui, "get_active_applications", list)
    editor = window.track_editors()[0]
    editor.set_track({"kind": kind, key: value})
    editor.refresh_devices()
    assert editor.get_track()[key] == value


def test_refresh_preserves_nondefault_available_source(window, monkeypatch):
    monkeypatch.setattr(gui, "get_devices", lambda **kwargs: [
        {"id": "other", "name": "Other"},
        {"id": "Fake Mic", "name": "Default"},
    ])
    editor = window.track_editors()[0]
    editor.set_track({"kind": "input", "input_id": "other"})
    editor.refresh_devices()
    assert editor.get_track()["input_id"] == "other"


def test_refresh_preserves_application_source(window, monkeypatch):
    app = {"pid": 987654, "name": "player.exe", "title": "Player"}
    monkeypatch.setattr(gui, "get_active_applications", lambda: [app])
    editor = window.track_editors()[0]
    editor.set_track({"kind": "application", "target_pid": app["pid"],
                      "target_name": app["title"]})
    editor.refresh_devices()
    assert editor.get_track()["target_pid"] == app["pid"]


def test_track_columns_align_and_only_apps_show_picker(window, monkeypatch):
    monkeypatch.setattr(gui, "get_active_applications", list)
    editors = [window.track_editors()[0], window.add_track(), window.add_track()]
    for editor, kind in zip(editors, ["input", "output", "application"], strict=True):
        editor.set_track({"kind": kind})
    window.resize(850, 650)
    window.show()
    window._test_app.processEvents()
    try:
        assert [not row.app_btn.isHidden() for row in editors] == [False, False, True]
        for attribute in ["kind_combo", "source_widget", "remove_btn"]:
            rects = [getattr(row, attribute).geometry() for row in editors]
            assert len({(rect.x(), rect.width(), rect.height()) for rect in rects}) == 1
        app = editors[-1]
        assert app.device_combo.geometry().right() < app.app_btn.geometry().left()
    finally:
        window.hide()


def test_picker_initial_items_keep_titles_and_handles(window, monkeypatch):
    monkeypatch.setattr(gui, "get_active_applications", lambda: [
        {"pid": 42, "hwnd": 101, "title": "Player", "name": "player.exe"},
    ])
    monkeypatch.setattr(gui, "_DWM_AVAILABLE", False)
    dialog = gui.AppPickerDialog(window)
    try:
        item = dialog._item_map[(42, 101)]
        assert item.text() == "Player\nplayer.exe"
        dialog._select((42, 101))
        assert dialog.selected_title == "Player"
        assert dialog.selected_hwnd == 101
    finally:
        dialog.close()


def test_dwm_properties_use_windows_bool_layout():
    import ctypes
    assert ctypes.sizeof(gui._DwmThumbnailProps) == 48
    assert gui._DwmThumbnailProps.fVisible.offset == 40
    assert gui._DwmThumbnailProps.fSourceClientAreaOnly.offset == 44


def test_both_output_modes_are_offered(window):
    modes = [window.combo_output_mode.itemData(i)
             for i in range(window.combo_output_mode.count())]
    assert modes == ["mixed", "separate_files"]
    assert window.get_settings()["output_mode"] == "mixed"


def test_tracks_and_mode_survive_a_save_load_round_trip(window, tmp_path):
    editor = window.add_track()
    editor.kind_combo.setCurrentText(gui.TrackEditor.KIND_OUTPUT)
    editor.refresh_devices()
    select_mode(window, "separate_files")

    saved = window.get_settings()
    assert saved["output_mode"] == "separate_files"
    assert len(saved["tracks"]) == 2

    with open(os.path.join(str(tmp_path), gui.CONFIG_FILE), "w") as f:
        json.dump(saved, f)

    # blank the UI, then reload it from disk
    window.clear_tracks()
    window.add_track()
    select_mode(window, "mixed")
    window.load_settings()

    reloaded = window.get_settings()
    assert reloaded["output_mode"] == "separate_files"
    assert reloaded["tracks"] == saved["tracks"]


def test_a_track_can_target_an_application(window, monkeypatch):
    monkeypatch.setattr(gui, "get_active_applications",
                        lambda: [{"pid": 4242, "name": "game.exe", "title": "Game"}])
    editor = window.track_editors()[0]

    editor.kind_combo.setCurrentText(gui.TrackEditor.KIND_APP)
    editor.refresh_devices()

    assert editor.get_track() == {"kind": "application", "target_pid": 4242,
                                  "target_name": "Game (game.exe)"}


def test_an_application_track_survives_a_save_load_round_trip(window, tmp_path, monkeypatch):
    monkeypatch.setattr(gui, "get_active_applications",
                        lambda: [{"pid": 4242, "name": "game.exe", "title": "Game"}])
    editor = window.track_editors()[0]
    editor.kind_combo.setCurrentText(gui.TrackEditor.KIND_APP)
    editor.refresh_devices()

    saved = window.get_settings()
    with open(os.path.join(str(tmp_path), gui.CONFIG_FILE), "w") as f:
        json.dump(saved, f)

    window.clear_tracks()
    window.add_track()
    window.load_settings()

    assert window.get_tracks() == saved["tracks"]
    assert window.get_tracks()[0]["kind"] == "application"


def test_choose_app_is_enabled_only_for_application_tracks(window):
    editor = window.track_editors()[0]

    assert editor.app_btn.isEnabled() is False

    editor.kind_combo.setCurrentText(gui.TrackEditor.KIND_APP)
    editor.refresh_devices()

    assert editor.app_btn.isEnabled() is True


def test_choosing_an_application_adopts_it_on_the_row(window, monkeypatch):
    class StubPicker:
        selected_pid = 4242
        selected_hwnd = 99
        selected_title = "Game"
        selected_name = "game.exe"

        def __init__(self, *args, **kwargs):
            pass

        def exec(self):
            return gui.QDialog.DialogCode.Accepted

    monkeypatch.setattr(gui, "AppPickerDialog", StubPicker)
    editor = window.track_editors()[0]

    editor.open_app_picker()

    assert editor.get_track() == {"kind": "application", "target_pid": 4242,
                                  "target_name": "Game (game.exe)"}


def test_a_cancelled_picker_changes_nothing(window, monkeypatch):
    class CancelledPicker:
        selected_pid = None
        selected_hwnd = None
        selected_title = None
        selected_name = None

        def __init__(self, *args, **kwargs):
            pass

        def exec(self):
            return gui.QDialog.DialogCode.Rejected

    monkeypatch.setattr(gui, "AppPickerDialog", CancelledPicker)
    editor = window.track_editors()[0]
    before = editor.get_track()

    editor.open_app_picker()

    assert editor.get_track() == before


def test_hotkeys_are_one_record_one_toggle_and_stop(window):
    settings = window.get_settings()

    for key in ("hk_record", "hk_toggle", "hk_stop"):
        assert key in settings
    for gone in ("hk_mic", "hk_loop", "hk_both"):
        assert gone not in settings


def test_an_old_per_mode_hotkey_becomes_the_record_hotkey(window, tmp_path):
    with open(os.path.join(str(tmp_path), gui.CONFIG_FILE), "w") as f:
        json.dump({"hk_both": "ctrl+shift+r", "hk_mic": "ctrl+m"}, f)

    window.load_settings()

    assert window.hk_record.text() == "ctrl+shift+r"
