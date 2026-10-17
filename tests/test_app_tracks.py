"""Application tracks: capture through proc-tap, and failing gracefully."""
import os
import sys
import threading
import types

import numpy as np
import soundfile as sf

import audio_recorder
from conftest import FakeDevice, dominant_freq
from test_tracks import THREE_TRACKS, record_tracks


class FakeSender:
    def __init__(self):
        self.messages = []

    def send(self, message):
        self.messages.append(message)


class StubAppRecorder(threading.Thread):
    """Stands in for ProcTapRecorder: writes a 1000 Hz tone, then stops."""

    def __init__(self, pid, filepath, name=None):
        super().__init__()
        self.pid = pid
        self.filepath = filepath
        self.device = types.SimpleNamespace(name=name or "application %s" % pid)
        self.samplerate = audio_recorder.PROC_TAP_SAMPLERATE
        self.channels = audio_recorder.PROC_TAP_CHANNELS
        self.stop_event = threading.Event()
        self.error = None
        self.frames = 0

    def probe(self):
        pass

    def run(self):
        t = np.arange(int(2 * self.samplerate)) / self.samplerate
        tone = 0.5 * np.sin(2 * np.pi * 1000 * t)
        data = np.repeat(tone[:, None], self.channels, axis=1).astype(np.float32)
        sf.write(self.filepath, data, self.samplerate, subtype="FLOAT")
        self.frames = len(data)

    def stop(self):
        self.stop_event.set()
        self.join()


def mic_only_fake():
    mic = FakeDevice("Mic A", freq=440.0, channels=1)
    return types.SimpleNamespace(
        default_microphone=lambda: mic,
        default_speaker=lambda: types.SimpleNamespace(name="Speakers A", id="spk-a"),
        all_speakers=lambda: [types.SimpleNamespace(name="Speakers A", id="spk-a")],
        all_microphones=lambda include_loopback=False: [mic],
        get_microphone=lambda id, include_loopback=False: mic,
    )


def test_decode_message_normalizes_both_shapes():
    assert audio_recorder._decode_message(b"abc") == ("data", b"abc")
    assert audio_recorder._decode_message(("data", b"x")) == ("data", b"x")
    assert audio_recorder._decode_message(("error", "closed", "gone")) == ("error", "closed", "gone")


def test_capture_worker_reports_a_missing_capture_library(monkeypatch):
    """proc-tap absent must produce a report, not an exception."""
    monkeypatch.setitem(sys.modules, "proctap", None)
    monkeypatch.setattr(audio_recorder, "_pid_alive", lambda pid: True)
    sender = FakeSender()

    audio_recorder._process_tap_worker(4242, sender)

    assert sender.messages
    kind, reason, detail = sender.messages[0]
    assert kind == "error"
    assert reason == "failed"
    assert detail


def test_hardware_only_recording_never_needs_the_capture_library(tmp_path, monkeypatch):
    monkeypatch.setitem(sys.modules, "proctap", None)
    monkeypatch.setattr(audio_recorder, "sc", mic_only_fake())

    path, error = record_tracks(tmp_path, [THREE_TRACKS[0]], output_format="wav")

    assert not error
    assert isinstance(path, str)


def test_an_application_track_keeps_its_own_file(tmp_path, monkeypatch):
    monkeypatch.setattr(audio_recorder, "sc", mic_only_fake())
    monkeypatch.setattr(audio_recorder, "ProcTapRecorder", StubAppRecorder)
    tracks = [{"kind": "input", "input_id": "Mic A"},
              {"kind": "application", "target_pid": 4242, "target_name": "Game"}]

    paths, error = record_tracks(tmp_path, tracks, output_format="wav",
                                 output_mode="separate_files")

    assert not error
    assert len(paths) == 2
    app_file = next(p for p in paths if "application" in os.path.basename(p))
    assert os.path.basename(app_file).endswith("_application.wav")
    data, sr = sf.read(app_file)
    assert abs(dominant_freq(data, sr) - 1000) < 10


def test_a_failing_application_track_is_reported_not_fatal(tmp_path, monkeypatch):
    """The other tracks still save, and the failure surfaces as an error."""
    monkeypatch.setattr(audio_recorder, "sc", mic_only_fake())

    class BrokenAppRecorder(StubAppRecorder):
        def run(self):
            self.error = "The application stopped or exited."

    monkeypatch.setattr(audio_recorder, "ProcTapRecorder", BrokenAppRecorder)
    tracks = [{"kind": "input", "input_id": "Mic A"},
              {"kind": "application", "target_pid": 4242, "target_name": "Gone"}]

    path, error = record_tracks(tmp_path, tracks, output_format="wav")

    assert path, "the microphone track should still have been saved"
    assert error and "Gone" in error
