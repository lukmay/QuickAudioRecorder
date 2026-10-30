import os

import numpy as np
import pytest
import soundfile as sf

from conftest import dominant_freq, record


def test_mic_to_wav(fake_sc, tmp_path):
    path, error = record(tmp_path, "mic", output_format="wav")
    assert not error
    data, sr = sf.read(path)
    assert len(data) > sr  # sped up fake device, should be several seconds
    assert abs(dominant_freq(data, sr) - 440) < 5


def test_loopback_to_mp3(fake_sc, tmp_path):
    path, error = record(tmp_path, "loopback", output_format="mp3")
    assert not error
    assert path.endswith(".mp3")
    data, sr = sf.read(path)
    assert abs(dominant_freq(data, sr) - 1000) < 5


def test_both_contains_mic_and_system_audio(fake_sc, tmp_path):
    path, error = record(tmp_path, "both", output_format="wav")
    assert not error
    data, sr = sf.read(path, always_2d=True)
    spectrum = np.abs(np.fft.rfft(data.mean(axis=1)))
    freqs = np.fft.rfftfreq(len(data), 1 / sr)
    peak = spectrum.max()
    for f in (440, 1000):
        assert spectrum[np.abs(freqs - f) < 3].max() > peak * 0.2, f"{f} Hz missing"


def test_normalize_lifts_quiet_recording(fake_sc, tmp_path):
    fake_sc.mic.amplitude = 0.05
    path, error = record(tmp_path, "mic", output_format="wav", normalize=True)
    assert not error
    data, _ = sf.read(path)
    assert np.abs(data).max() > 0.9


def test_device_failure_is_reported(fake_sc, tmp_path):
    fake_sc.mic.fail_with = RuntimeError("invalid argument")
    path, error = record(tmp_path, "mic", seconds=0.1, output_format="wav")
    assert error
    assert "invalid argument" in error


def test_temp_files_are_cleaned_up(fake_sc, tmp_path, monkeypatch):
    import tempfile
    tmp = tmp_path / "tmp"
    tmp.mkdir()
    monkeypatch.setattr(tempfile, "tempdir", str(tmp))
    out = tmp_path / "out"
    path, error = record(out, "both", output_format="mp3")
    assert not error
    assert os.listdir(tmp) == []


def test_loopback_stays_stereo(fake_sc, tmp_path):
    path, error = record(tmp_path, "loopback", output_format="wav")
    assert not error
    assert sf.info(path).channels == 2


def test_both_with_different_device_rates(fake_sc, tmp_path):
    fake_sc.mic.rates = {44100}
    fake_sc.loop.rates = {48000}
    path, error = record(tmp_path, "both", output_format="wav")
    assert not error
    assert sf.info(path).samplerate == 44100


def test_mix_resamples_second_source(tmp_path):
    import audio_recorder
    a, b = tmp_path / "a.wav", tmp_path / "b.wav"
    sf.write(a, 0.5 * np.sin(2 * np.pi * 440 * np.arange(44100 * 2) / 44100), 44100)
    sf.write(b, 0.5 * np.sin(2 * np.pi * 1000 * np.arange(48000 * 2) / 48000), 48000)

    rec = audio_recorder.AudioRecorder("x", "both", str(tmp_path), output_format="wav")
    out = tmp_path / "out.wav"
    with sf.SoundFile(a) as fa, sf.SoundFile(b) as fb:
        rec._write_output([fa, fb], str(out))
    data, sr = sf.read(out)
    assert sr == 44100
    spectrum = np.abs(np.fft.rfft(data))
    freqs = np.fft.rfftfreq(len(data), 1 / sr)
    # the 48 kHz source must not come out pitched down
    assert spectrum[np.abs(freqs - 1000) < 3].max() > spectrum.max() * 0.3


def test_mix_stretches_small_clock_drift(tmp_path):
    import audio_recorder
    sr = 8000
    t = np.arange(sr * 60) / sr
    a = tmp_path / "a.wav"
    b = tmp_path / "b.wav"
    sf.write(a, np.sin(2 * np.pi * 100 * t), sr)
    # second device ran 0.2% slow: fewer frames for the same wall time
    sf.write(b, np.sin(2 * np.pi * 100 * t[: int(len(t) * 0.998)]), sr)

    rec = audio_recorder.AudioRecorder("x", "both", str(tmp_path), output_format="wav")
    out = tmp_path / "out.wav"
    with sf.SoundFile(a) as fa, sf.SoundFile(b) as fb:
        rec._write_output([fa, fb], str(out))
    data, _ = sf.read(out)
    assert len(data) == len(t)
    # without stretching, b would end 0.12 s early and leave a silent tail
    assert np.abs(data[-sr // 10:]).max() > 0.5


def test_flac_output(fake_sc, tmp_path):
    path, error = record(tmp_path, "loopback", output_format="flac")
    assert not error
    info = sf.info(path)
    assert path.endswith(".flac") and info.format == "FLAC"
    data, sr = sf.read(path)
    assert abs(dominant_freq(data, sr) - 1000) < 5


@pytest.mark.parametrize("fmt", ["mp3", "wav", "flac"])
def test_mono_output(fake_sc, tmp_path, fmt):
    path, error = record(tmp_path, "both", output_format=fmt, mono=True)
    assert not error
    assert sf.info(path).channels == 1


def test_empty_device_errors_still_say_something(fake_sc, tmp_path, monkeypatch):
    import audio_recorder
    monkeypatch.setattr(audio_recorder.PortAudioDevice, "find", classmethod(lambda cls, name: None))
    fake_sc.mic.fail_with = AssertionError()
    path, error = record(tmp_path, "mic", seconds=0.1, output_format="wav")
    assert "AssertionError" in error


def test_normalize_levels_quiet_mic_against_loud_system_audio(fake_sc, tmp_path):
    fake_sc.mic.amplitude = 0.02
    fake_sc.loop.amplitude = 0.8
    path, error = record(tmp_path, "both", output_format="wav", normalize=True)
    assert not error
    data, sr = sf.read(path, always_2d=True)
    spectrum = np.abs(np.fft.rfft(data.mean(axis=1)))
    freqs = np.fft.rfftfreq(len(data), 1 / sr)
    mic = spectrum[np.abs(freqs - 440) < 3].max()
    system = spectrum[np.abs(freqs - 1000) < 3].max()
    assert 0.5 < mic / system < 2


@pytest.mark.parametrize("normalize", [False, True])
def test_saving_reports_progress_up_to_done(fake_sc, tmp_path, normalize):
    steps = []
    path, error = record(tmp_path, "both", output_format="mp3", normalize=normalize,
                         on_progress=steps.append)
    assert not error
    assert steps == sorted(steps)
    assert steps[-1] == 1.0
