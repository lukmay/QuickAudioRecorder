"""The frozen PortAudio payload must match Python's native architecture."""
import os
import runpy
import sys
import sysconfig
import types
from pathlib import Path

import pytest

HOOK = Path(__file__).resolve().parents[1] / "packaging/hooks/hook-sounddevice.py"


@pytest.mark.parametrize("platform,dll", [
    ("win-amd64", "libportaudio64bit.dll"),
    ("win-arm64", "libportaudioarm64.dll"),
])
def test_only_native_non_asio_dll_is_collected(tmp_path, monkeypatch, platform, dll):
    folder = tmp_path / "_sounddevice_data/portaudio-binaries"
    folder.mkdir(parents=True)
    for name in [dll, "libportaudio32bit.dll", "libportaudio64bit-asio.dll"]:
        (folder / name).touch()
    (folder / "README.md").touch()
    hooks = types.ModuleType("PyInstaller.utils.hooks")
    monkeypatch.setattr(hooks, "get_module_file_attribute", lambda name: str(tmp_path / "sounddevice.py"), raising=False)
    monkeypatch.setitem(sys.modules, "PyInstaller.utils.hooks", hooks)
    monkeypatch.setattr(sysconfig, "get_platform", lambda: platform)
    result = runpy.run_path(str(HOOK))
    assert result["binaries"] == [(str(folder / dll), "_sounddevice_data/portaudio-binaries")]


@pytest.mark.parametrize("frozen", [False, True])
@pytest.mark.parametrize("setting", ["0", "1"])
def test_entry_point_ignores_asio_only_when_frozen(monkeypatch, frozen, setting):
    monkeypatch.setattr(sys, "frozen", frozen, raising=False)
    monkeypatch.setenv("SD_ENABLE_ASIO", setting)
    runpy.run_path(str(HOOK.parents[2] / "main.py"), run_name="packaging_test")
    assert os.environ.get("SD_ENABLE_ASIO") == (None if frozen else setting)


def test_missing_native_dll_fails_packaging(tmp_path, monkeypatch):
    hooks = types.ModuleType("PyInstaller.utils.hooks")
    monkeypatch.setattr(hooks, "get_module_file_attribute", lambda name: str(tmp_path / "sounddevice.py"), raising=False)
    monkeypatch.setitem(sys.modules, "PyInstaller.utils.hooks", hooks)
    monkeypatch.setattr(sysconfig, "get_platform", lambda: "win-arm64")
    with pytest.raises(RuntimeError, match="Missing native PortAudio"):
        runpy.run_path(str(HOOK))
