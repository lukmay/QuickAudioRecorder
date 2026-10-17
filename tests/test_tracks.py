"""Track list, simultaneous capture, and mixed/separate output."""
import os
import threading
import time
import types

import numpy as np
import pytest
import soundfile as sf

import audio_recorder
from conftest import FakeDevice, dominant_freq


def multi_device_fake():
    """Two microphones and two speakers, each with its own tone."""
    mic_a = FakeDevice("Mic A", freq=440.0, channels=1)
    mic_b = FakeDevice("Mic B", freq=660.0, channels=1)
    speaker_a = types.SimpleNamespace(name="Speakers A", id="spk-a")
    speaker_b = types.SimpleNamespace(name="Speakers B", id="spk-b")
    loop_a = FakeDevice("Speakers A", freq=1000.0, isloopback=True)
    loop_b = FakeDevice("Speakers B", freq=1500.0, isloopback=True)
    mics = {"Mic A": mic_a, "Mic B": mic_b}
    return types.SimpleNamespace(
        default_microphone=lambda: mic_a,
        default_speaker=lambda: speaker_a,
        all_speakers=lambda: [speaker_a, speaker_b],
        all_microphones=lambda include_loopback=False: (
            [mic_a, mic_b, loop_a, loop_b] if include_loopback else [mic_a, mic_b]),
        get_microphone=lambda id, include_loopback=False: mics.get(id, mic_a),
    )


def record_tracks(tmp_path, tracks, seconds=0.5, **kwargs):
    done = threading.Event()
    result = {}

    def on_finish(path, error):
        result["path"], result["error"] = path, error
        done.set()

    rec = audio_recorder.AudioRecorder(
        None, None, str(tmp_path), tracks=tracks, on_finish_callback=on_finish, **kwargs)
    rec.start()
    time.sleep(seconds)
    rec.stop()
    assert done.wait(30), "recorder did not finish"
    return result["path"], result["error"]


THREE_TRACKS = [
    {"kind": "input", "input_id": "Mic A"},
    {"kind": "output", "speaker_id": "spk-a"},
    {"kind": "output", "speaker_id": "spk-b"},
]


def test_legacy_modes_map_onto_tracks():
    assert audio_recorder.tracks_from_mode("m", "mic") == [{"kind": "input", "input_id": "m"}]
    assert audio_recorder.tracks_from_mode("m", "loopback") == [{"kind": "output", "speaker_id": None}]
    assert [t["kind"] for t in audio_recorder.tracks_from_mode("m", "both")] == ["input", "output"]


def test_track_and_mode_validation():
    with pytest.raises(ValueError):
        audio_recorder.normalize_tracks([])
    with pytest.raises(ValueError):
        audio_recorder.normalize_tracks([{"kind": "telepathy"}])
    with pytest.raises(ValueError):
        audio_recorder.normalize_output_mode("mangled")
    assert audio_recorder.normalize_output_mode("SEPARATE_FILES") == "separate_files"


def test_three_tracks_are_captured_simultaneously(tmp_path, monkeypatch):
    monkeypatch.setattr(audio_recorder, "sc", multi_device_fake())

    paths, error = record_tracks(tmp_path, THREE_TRACKS, output_format="wav",
                                 output_mode="separate_files")

    assert not error
    assert isinstance(paths, list)
    assert len(paths) == 3
    # every track keeps its own audio, and no two tracks share a filename
    assert len(set(paths)) == 3
    found = []
    for p in paths:
        data, sr = sf.read(p)
        found.append(round(dominant_freq(data, sr)))
    assert sorted(found) == [440, 1000, 1500]


def test_separate_mode_names_each_track(tmp_path, monkeypatch):
    monkeypatch.setattr(audio_recorder, "sc", multi_device_fake())

    paths, error = record_tracks(tmp_path, THREE_TRACKS, output_format="wav",
                                 output_mode="separate_files")

    assert not error
    names = sorted(os.path.basename(p) for p in paths)
    assert any(n.endswith("_input.wav") for n in names)
    assert sum(1 for n in names if "_output" in n) == 2


def test_separate_files_share_the_mix_rate(tmp_path, monkeypatch):
    monkeypatch.setattr(audio_recorder, "sc", multi_device_fake())

    paths, error = record_tracks(tmp_path, THREE_TRACKS, output_format="wav",
                                 output_mode="separate_files")

    assert not error
    assert {sf.info(p).samplerate for p in paths} == {44100}


def test_mixed_mode_writes_one_file_containing_every_track(tmp_path, monkeypatch):
    monkeypatch.setattr(audio_recorder, "sc", multi_device_fake())

    path, error = record_tracks(tmp_path, THREE_TRACKS, output_format="wav",
                                output_mode="mixed")

    assert not error
    assert isinstance(path, str)
    assert len(os.listdir(tmp_path)) == 1
    data, sr = sf.read(path)
    if data.ndim > 1:
        data = data.mean(axis=1)
    spectrum = np.abs(np.fft.rfft(data))
    freqs = np.fft.rfftfreq(len(data), 1 / sr)
    for wanted in (440, 1000, 1500):
        assert spectrum[np.abs(freqs - wanted) < 3].max() > 0


def test_separate_mode_with_a_single_track_writes_one_file(tmp_path, monkeypatch):
    monkeypatch.setattr(audio_recorder, "sc", multi_device_fake())

    path, error = record_tracks(tmp_path, [THREE_TRACKS[0]], output_format="wav",
                                output_mode="separate_files")

    assert not error
    assert isinstance(path, str)
