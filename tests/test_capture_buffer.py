"""Probe and capture must use the same explicit SoundCard buffer."""
import contextlib

import numpy as np

import audio_recorder as ar


def test_soundcard_probe_and_capture_use_scheduling_headroom(tmp_path):
    calls = []
    recorder = None

    class Device:
        name = 'VAIO3'
        channels = 2

        @contextlib.contextmanager
        def recorder(self, **kwargs):
            calls.append(kwargs)

            def record(numframes):
                assert recorder is not None
                recorder.stop_event.set()
                return np.zeros((numframes, 2), dtype=np.float32)

            yield type('Stream', (), {'record': staticmethod(record)})()

    recorder = ar.RawRecorder(Device(), str(tmp_path / 'capture.wav'))
    recorder.probe()
    recorder.run()
    assert recorder.error is None
    assert recorder.frames == 2048
    assert len(calls) == 2
    assert all(call == {'samplerate': 44100, 'channels': 2, 'blocksize': 8192}
               for call in calls)


def test_sounddevice_fallback_keeps_its_existing_interface(monkeypatch, tmp_path):
    calls = []

    @contextlib.contextmanager
    def stream(self, samplerate, channels):
        calls.append((samplerate, channels))
        yield None

    monkeypatch.setattr(ar.PortAudioDevice, 'recorder', stream)
    recorder = ar.RawRecorder(ar.PortAudioDevice('USB mic', 1, 2), str(tmp_path / 'mic.wav'))
    recorder.probe()
    assert calls == [(44100, 2)]
