"""A device that disappears in the middle of a recording."""

import os
import threading

import soundfile as sf

import audio_recorder
from conftest import dominant_freq, record


def test_unplug_keeps_the_partial_recording(fake_sc, tmp_path):
    fake_sc.mic.die_after = 44100
    path, error = record(tmp_path, "mic", seconds=2.0, output_format="wav")

    data, sr = sf.read(path)
    assert len(data) >= 44100, "the audio captured before the unplug is gone"
    assert abs(dominant_freq(data, sr) - 440) < 5
    assert "stopped early" in error


def test_recording_stops_by_itself_when_the_device_goes(fake_sc, tmp_path):
    """Nobody presses stop: losing the device alone must end the recording."""
    fake_sc.mic.die_after = 44100
    done = threading.Event()
    result = {}

    def on_finish(path, error):
        result["path"], result["error"] = path, error
        done.set()

    rec = audio_recorder.AudioRecorder(
        mic_id="Fake Mic", source_mode="mic", output_folder=str(tmp_path),
        output_format="wav", on_finish_callback=on_finish)
    rec.start()
    assert done.wait(10), "the recording kept running after the device was lost"
    assert os.path.exists(result["path"])
    assert "stopped early" in result["error"]


def test_unplug_in_both_mode_still_saves_the_system_audio(fake_sc, tmp_path):
    fake_sc.mic.die_after = 44100
    path, error = record(tmp_path, "both", seconds=2.0, output_format="wav")

    data, sr = sf.read(path, always_2d=True)
    # the system audio ran on until the recording stopped, so it dominates the mix
    assert abs(dominant_freq(data, sr) - 1000) < 5
    assert "stopped early" in error


def test_unplug_before_any_audio_still_fails(fake_sc, tmp_path):
    fake_sc.mic.die_after = 0
    path, error = record(tmp_path, "mic", output_format="wav")

    assert not path
    assert "device lost" in error
