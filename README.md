# Quick Audio Recorder

**Quick Audio Recorder** is a minimalist, yet powerful tool for Windows to quickly record audio from your microphone, system audio (loopback), or both simultaneously.  
It sits quietly in your system tray and is always ready with a single click or global hotkey.

<img width="393" height="156" alt="image" src="https://github.com/user-attachments/assets/7e3bfcaf-6f58-4404-b85a-4ba0b6fea085" />

## Contributing

If you've forked this and built something useful on top, PRs are very welcome. Same goes for bug reports, open an issue, even if it's just "doesn't work on my machine, here's what I tried."

A few things I'd find handy but haven't gotten around to: Linux/macOS support, more export formats, a proper config file instead of the current settings dialog. But honestly, anything you think makes the tool better is fair game. Keep it small and focused so it's easy to review.

Before opening a larger PR, please check [CONTRIBUTING.md](CONTRIBUTING.md) for what's in scope and what to include.

[![PRs Welcome](https://img.shields.io/badge/PRs-welcome-brightgreen.svg)](https://github.com/lukmay/QuickAudioRecorder/pulls)
[![Issues](https://img.shields.io/github/issues/lukmay/QuickAudioRecorder)](https://github.com/lukmay/QuickAudioRecorder/issues)

## Features

### My favourite use case 💖

Quickly recording audio and pasting it anywhere, e.g. into Anki cards.  
*(Files can be set to auto-delete, so nothing stays on disk, the clip just lands in your clipboard.)*

https://github.com/user-attachments/assets/14238816-c1ee-4914-9211-5f0007bfe999



### Settings Showcase

<img width="575" height="724" alt="image" src="https://github.com/user-attachments/assets/b35131bc-1ff8-41e1-87b5-1e472f9da981" />


-   **Modes:**
    -   🎤 **Microphone:** Record your voice.
    -   🔊 **System Audio:** Record what you hear (Loopback).
    -   🎙️+🔊 **Both:** Record both tracks simultaneously (mixed).
-   **Output:** MP3, WAV or FLAC, optionally saved as mono for smaller voice clips.
-   **Post-Processing:**
    -   **Auto-Normalize:** Automatically adjusts volume to optimal levels after recording. In "Both" mode, mic and system audio are leveled separately first, so a quiet voice is not buried under loud system audio.
    -   **Clipboard Integration:** Automatically copies the file (or file path) to your clipboard.
    -   **Clean Workflow:** Option to move the file to a temp folder and copy it, keeping your desktop clean.
-   **Control:**
    -   **Global Hotkeys:** Start/Stop recording from anywhere (e.g., `Ctrl+Alt+R`).
    -   **Tray Icon:** Left-click to toggle recording immediately.
    -   **Visual Feedback:** Tray icon turns red while recording (hover it to see how long) and orange while saving, filling up as the file gets written.
    -   **Auto-Stop:** Optionally stop and save after a set number of minutes.
-   **Unplug Safety:** If a device is lost mid-recording (unplugged mic, headset out of range), the recording stops and everything captured up to that point is saved. The tray shows which device was lost.

## Installation

1.  Go to the [Releases](https://github.com/lukmay/QuickAudioRecorder/releases) page.
2.  Download `QuickAudioRecorder.exe`.
3.  Run it! (No installation required).

The exe is built by [GitHub Actions](.github/workflows/release.yml) straight from the tagged source, and each release has a `.sha256` file to check the download against. Some antivirus scanners flag PyInstaller apps in general. If you would rather not trust a binary, [build it yourself](#build-from-source).

## Usage

1.  **Right-click** the tray icon to open **Settings**.
2.  Select your **Microphone** and **Output Folder**.
3.  Set your **Hotkeys** (optional).
4.  **Left-click** the tray icon or use a hotkey to start recording.
5.  Click again to stop. The file is saved and ready to use!

## Development

### Requirements
-   Python 3.12+
-   `pip install PyQt6 soundcard sounddevice soundfile numpy lameenc keyboard`

### Tests
```bash
pip install pytest
python -m pytest
```
The tests use fake audio devices, so they run anywhere. If [VB-Audio Virtual Cable](https://vb-audio.com/Cable/) is installed, `tests/test_cable.py` also records a real tone through the cable without touching your default speakers or microphone.

### Build from Source
To create the standalone executable:
```bash
pip install pyinstaller
pyinstaller --noconsole --onefile --noupx --name QuickAudioRecorder main.py
```

## License

[MIT](LICENSE)
