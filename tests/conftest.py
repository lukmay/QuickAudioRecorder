import os
import sys
import threading
import time
import types

import numpy as np
import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import audio_recorder


class FakeStream:
    def __init__(self, device, samplerate, channels):
        self.device = device
        self.samplerate = samplerate
        self.channels = channels
        self.pos = 0

    def __enter__(self):
        if self.device.fail_with is not None:
            raise self.device.fail_with
        return self

    def __exit__(self, *exc):
        return False

    def record(self, numframes):
        if self.device.die_after is not None and self.pos >= self.device.die_after:
            raise RuntimeError("device lost")
        # pace like a real device so a recording thread doesn't spin
        time.sleep(numframes / self.samplerate / self.device.speed)
        t = (np.arange(numframes) + self.pos) / self.samplerate
        self.pos += numframes
        tone = self.device.amplitude * np.sin(2 * np.pi * self.device.freq * t)
        return np.repeat(tone[:, None], self.channels, axis=1).astype(np.float32)


class FakeDevice:
    """Stands in for a soundcard microphone / loopback device."""

    def __init__(self, name, freq=440.0, amplitude=0.5, channels=2,
                 isloopback=False, speed=20.0, fail_with=None, rates=None,
                 die_after=None):
        self.name = name
        self.id = name
        self.freq = freq
        self.amplitude = amplitude
        self.channels = channels
        self.isloopback = isloopback
        self.speed = speed
        self.fail_with = fail_with
        self.rates = rates
        self.die_after = die_after  # frames recorded before it gets unplugged

    def recorder(self, samplerate, channels=None, blocksize=None):
        if self.rates is not None and samplerate not in self.rates:
            raise RuntimeError("invalid argument")
        return FakeStream(self, samplerate, channels or self.channels)


@pytest.fixture
def fake_sc(monkeypatch):
    """Replaces the soundcard module used by audio_recorder with fake devices."""
    mic = FakeDevice("Fake Mic", freq=440.0, channels=1)
    speaker = types.SimpleNamespace(name="Fake Speakers", id="spk")
    loop = FakeDevice("Fake Speakers", freq=1000.0, isloopback=True)

    fake = types.SimpleNamespace(
        mic=mic,
        loop=loop,
        default_speaker=lambda: speaker,
        default_microphone=lambda: mic,
        all_microphones=lambda include_loopback=False: [mic, loop] if include_loopback else [mic],
        get_microphone=lambda id, include_loopback=False: mic,
    )
    monkeypatch.setattr(audio_recorder, "sc", fake)
    return fake


def record(tmp_path, mode, seconds=0.5, **kwargs):
    """Runs a full AudioRecorder session and returns (path, error)."""
    done = threading.Event()
    result = {}

    def on_finish(path, error):
        result["path"], result["error"] = path, error
        done.set()

    rec = audio_recorder.AudioRecorder(
        mic_id="Fake Mic", source_mode=mode, output_folder=str(tmp_path),
        on_finish_callback=on_finish, **kwargs)
    rec.start()
    time.sleep(seconds)
    rec.stop()
    assert done.wait(30), "recorder did not finish"
    return result["path"], result["error"]


def dominant_freq(data, samplerate):
    if data.ndim > 1:
        data = data.mean(axis=1)
    spectrum = np.abs(np.fft.rfft(data))
    return np.fft.rfftfreq(len(data), 1 / samplerate)[np.argmax(spectrum)]
