"""Collect only the matching WASAPI PortAudio DLL (no ASIO or other CPUs)."""
import sysconfig
from pathlib import Path

from PyInstaller.utils.hooks import get_module_file_attribute

platform = sysconfig.get_platform()
names = {"win-amd64": "libportaudio64bit.dll", "win-arm64": "libportaudioarm64.dll"}
if platform not in names:
    raise RuntimeError(f"Unsupported Windows release architecture: {platform}")
module_file = get_module_file_attribute("sounddevice")
if module_file is None:
    raise RuntimeError("sounddevice module location is unavailable")
folder = Path(module_file).parent / "_sounddevice_data" / "portaudio-binaries"
library = folder / names[platform]
if not library.is_file():
    raise RuntimeError(f"Missing native PortAudio library: {library}")
binaries = [(str(library), "_sounddevice_data/portaudio-binaries")]
datas = [(str(folder / "README.md"), "_sounddevice_data/portaudio-binaries")]
