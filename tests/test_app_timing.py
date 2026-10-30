"""Application timeline placement must not depend on pipe timeout count."""
import multiprocessing
import threading
import types

import numpy as np
import pytest
import soundfile as sf

import audio_recorder


@pytest.mark.parametrize("timeouts", [0, 3, 10])
def test_delayed_delivery_does_not_insert_silence(tmp_path, monkeypatch, timeouts):
    output = tmp_path / "app.wav"
    recorder = audio_recorder.ProcTapRecorder(123, str(output))
    # 300 ms of already captured audio, delivered after arbitrary pipe stalls.
    data = np.full((14400, 2), 0.25, dtype=np.float32)
    pending = [False] * timeouts + [True]

    class Receiver:
        def poll(self, timeout):
            return pending.pop(0)

        def recv(self):
            recorder.stop_event.set()
            return ("data", data.tobytes(), 100.3)

        def close(self):
            pass

    process = types.SimpleNamespace(start=lambda: None, is_alive=lambda: True,
                                    terminate=lambda: None, join=lambda **kw: None)
    monkeypatch.setattr(multiprocessing, "Pipe", lambda **kw: (
        Receiver(), types.SimpleNamespace(close=lambda: None)))
    monkeypatch.setattr(multiprocessing, "get_context", lambda *a: types.SimpleNamespace(
        Process=lambda **kw: process))
    clock = iter([100.0, 100.3])
    monkeypatch.setattr(audio_recorder.time, "monotonic", lambda: next(clock))
    recorder.run()
    captured, rate = sf.read(output)
    assert recorder.error is None
    assert rate == 48000
    assert len(captured) == len(data)
    np.testing.assert_allclose(captured, data, atol=1e-4)


@pytest.mark.parametrize("shared_epoch", [None, 101.5])
def test_ready_message_excludes_worker_startup_from_timeline(tmp_path, monkeypatch, shared_epoch):
    output = tmp_path / "ready.wav"
    recorder = audio_recorder.ProcTapRecorder(123, str(output))
    data = np.full((4800, 2), 0.25, dtype=np.float32)
    if shared_epoch is not None:
        recorder.session_epoch = shared_epoch
        gate = threading.Event()
        gate.set()
        recorder.session_started = gate
    messages = iter([("ready", 102.0), ("data", data.tobytes(), 102.1)])

    class Receiver:
        def poll(self, timeout):
            return True

        def recv(self):
            message = next(messages)
            if message[0] == "data":
                recorder.stop_event.set()
            return message

        def close(self):
            pass

    process = types.SimpleNamespace(start=lambda: None, terminate=lambda: None,
                                    join=lambda **kw: None)
    monkeypatch.setattr(multiprocessing, "Pipe", lambda **kw: (
        Receiver(), types.SimpleNamespace(close=lambda: None)))
    monkeypatch.setattr(multiprocessing, "get_context", lambda *a: types.SimpleNamespace(
        Process=lambda **kw: process))
    clock = iter([100.0, 102.1])
    monkeypatch.setattr(audio_recorder.time, "monotonic", lambda: next(clock))
    recorder.run()
    captured, _ = sf.read(output)
    assert recorder.error is None
    assert recorder.ready_event.is_set()
    gap = 24000 if shared_epoch is not None else 0
    assert len(captured) == gap + 4800
    assert not np.any(captured[:gap])
    np.testing.assert_allclose(captured[gap:], data, atol=1e-4)


def test_entirely_quiet_application_keeps_session_duration(tmp_path, monkeypatch):
    output = tmp_path / "quiet.wav"
    recorder = audio_recorder.ProcTapRecorder(123, str(output))

    class Receiver:
        def poll(self, timeout):
            recorder.stop_event.set()
            return False

        def close(self):
            pass

    process = types.SimpleNamespace(start=lambda: None, is_alive=lambda: True,
                                    terminate=lambda: None, join=lambda **kw: None)
    monkeypatch.setattr(multiprocessing, "Pipe", lambda **kw: (
        Receiver(), types.SimpleNamespace(close=lambda: None)))
    monkeypatch.setattr(multiprocessing, "get_context", lambda *a: types.SimpleNamespace(
        Process=lambda **kw: process))
    clock = iter([100.0, 102.0])
    monkeypatch.setattr(audio_recorder.time, "monotonic", lambda: next(clock))
    recorder.run()
    captured, rate = sf.read(output)
    assert recorder.error is None
    assert recorder.chunks_read == 0
    assert len(captured) == 2 * rate
    assert not np.any(captured)


@pytest.mark.parametrize("gap_seconds", [0.05, 2.0])
def test_genuine_quiet_gap_is_preserved(tmp_path, monkeypatch, gap_seconds):
    output = tmp_path / "app.wav"
    recorder = audio_recorder.ProcTapRecorder(123, str(output))
    data = np.full((4800, 2), 0.25, dtype=np.float32)

    class Receiver:
        def poll(self, timeout):
            return True

        def recv(self):
            recorder.stop_event.set()
            return ("data", data.tobytes(), 100.1 + gap_seconds)

        def close(self):
            pass

    process = types.SimpleNamespace(start=lambda: None, terminate=lambda: None,
                                    join=lambda **kw: None)
    monkeypatch.setattr(multiprocessing, "Pipe", lambda **kw: (
        Receiver(), types.SimpleNamespace(close=lambda: None)))
    monkeypatch.setattr(multiprocessing, "get_context", lambda *a: types.SimpleNamespace(
        Process=lambda **kw: process))
    clock = iter([100.0, 100.1 + gap_seconds])
    monkeypatch.setattr(audio_recorder.time, "monotonic", lambda: next(clock))
    recorder.run()
    captured, _ = sf.read(output)
    assert recorder.error is None
    gap = round(gap_seconds * 48000)
    assert len(captured) == gap + 4800
    assert not np.any(captured[:gap])
    np.testing.assert_allclose(captured[gap:], data, atol=1e-4)
