import contextlib
import os
import queue
import tempfile
import threading
import time
import types

import lameenc
import numpy as np
import soundcard as sc
import soundfile as sf

from soundcard_compat import apply_soundcard_compat

apply_soundcard_compat()

# Track kinds and output modes for the track list.
TRACK_KIND_INPUT = "input"
TRACK_KIND_OUTPUT = "output"
TRACK_KIND_APP = "application"
TRACK_KINDS = (TRACK_KIND_INPUT, TRACK_KIND_OUTPUT, TRACK_KIND_APP)
TRACK_LABELS = {TRACK_KIND_INPUT: "input", TRACK_KIND_OUTPUT: "output",
                TRACK_KIND_APP: "application"}
VALID_OUTPUT_MODES = ("mixed", "separate_files")


def normalize_output_mode(output_mode):
    mode = str(output_mode or "mixed").strip().lower()
    if mode not in VALID_OUTPUT_MODES:
        raise ValueError(f"Unsupported output mode: {output_mode}")
    return mode


def normalize_tracks(tracks):
    """Validates a track list and fills in defaults. Returns a new list."""
    if not tracks:
        raise ValueError("At least one track is required")
    cleaned = []
    for track in tracks:
        if not isinstance(track, dict):
            raise ValueError(f"Unsupported track: {track!r}")
        kind = str(track.get("kind") or TRACK_KIND_INPUT).strip().lower()
        if kind not in TRACK_KINDS:
            raise ValueError(f"Unsupported track kind: {track.get('kind')}")
        entry = {"kind": kind}
        if kind == TRACK_KIND_INPUT:
            entry["input_id"] = track.get("input_id")
        elif kind == TRACK_KIND_OUTPUT:
            entry["speaker_id"] = track.get("speaker_id")
        else:
            entry["target_pid"] = track.get("target_pid")
            entry["target_name"] = track.get("target_name")
        cleaned.append(entry)
    return cleaned


def tracks_from_mode(mic_id, source_mode):
    """Maps the older single-source interface onto a track list."""
    mode = str(source_mode or "mic").strip().lower()
    if mode == "loopback":
        return [{"kind": TRACK_KIND_OUTPUT, "speaker_id": None}]
    if mode == "both":
        return [{"kind": TRACK_KIND_INPUT, "input_id": mic_id},
                {"kind": TRACK_KIND_OUTPUT, "speaker_id": None}]
    return [{"kind": TRACK_KIND_INPUT, "input_id": mic_id}]


class PortAudioDevice:
    """
    Fallback for microphones soundcard can't open. soundcard only handles
    devices whose Windows mix format is float, some USB mics report 16 bit
    PCM and fail with an empty AssertionError (#11, bastibe/SoundCard#93).
    Mirrors the small part of the soundcard device API that RawRecorder uses.
    """
    def __init__(self, name, index, channels):
        self.name = name
        self.index = index
        self.channels = channels

    @classmethod
    def find(cls, name):
        try:
            import sounddevice as sd
        except Exception:
            return None
        wasapi = [i for i, h in enumerate(sd.query_hostapis()) if "WASAPI" in h["name"]]
        for i, d in enumerate(sd.query_devices()):
            if d["hostapi"] in wasapi and d["max_input_channels"] > 0 and d["name"] == name:
                return cls(name, i, d["max_input_channels"])
        return None

    @contextlib.contextmanager
    def recorder(self, samplerate, channels):
        import sounddevice as sd
        # Callback mode: blocking reads of 2048 frames dropped audio over WASAPI
        blocks = queue.Queue()
        stream = sd.InputStream(device=self.index, samplerate=samplerate, channels=channels,
                                dtype="float32", extra_settings=sd.WasapiSettings(auto_convert=True),
                                callback=lambda data, frames, t, status: blocks.put(data.copy()))
        pending = np.zeros((0, channels), dtype="float32")

        def record(numframes):
            nonlocal pending
            while len(pending) < numframes:
                pending = np.concatenate((pending, blocks.get(timeout=2)))
            data, pending = pending[:numframes], pending[numframes:]
            return data

        stream.start()
        try:
            yield types.SimpleNamespace(record=record)
        finally:
            stream.stop()
            stream.close()

class RawRecorder(threading.Thread):
    """
    Helper thread to record a single device to a WAV file.
    """
    def __init__(self, device, filepath, samplerate=44100, channels=2):
        super().__init__()
        self.device = device
        self.filepath = filepath
        self.samplerate = samplerate
        self.channels = channels
        self.stop_event = threading.Event()
        self.error = None
        self.frames = 0  # written so far, kept if the device is lost midway

    def _open_stream(self, samplerate, channels):
        # SoundCard's default WASAPI buffer can be shorter than one read.
        # Leave scheduling headroom (VAIO3 drops packets with the default at
        # 44.1 kHz) while keeping the existing 2048-frame read/stop cadence.
        options = {} if isinstance(self.device, PortAudioDevice) else {"blocksize": 8192}
        return self.device.recorder(samplerate=samplerate, channels=channels, **options)

    def _probe_params(self):
        """Find working (samplerate, channels) for this device."""
        channels = self.channels
        if hasattr(self.device, 'channels'):
            channels = min(self.channels, self.device.channels)

        # requested values first, fallbacks after (dict keeps order, drops dupes)
        rates = dict.fromkeys([self.samplerate, 48000, 44100])
        channel_options = dict.fromkeys([channels, 1])

        failures = []
        for sr in rates:
            for ch in channel_options:
                try:
                    with self._open_stream(samplerate=sr, channels=ch):
                        return sr, ch
                except Exception as e:
                    # soundcard raises a bare AssertionError for some mics, keep the type visible
                    failures.append(f"  {sr}Hz/{ch}ch: {str(e) or type(e).__name__}")

        if not isinstance(self.device, PortAudioDevice):
            fallback = PortAudioDevice.find(self.device.name)
            if fallback:
                print(f"soundcard can't open '{self.device.name}', trying PortAudio")
                self.device = fallback
                return self._probe_params()

        raise Exception(
            f"Device '{self.device.name}' rejected all parameter combinations:\n"
            + "\n".join(failures)
        )

    def probe(self):
        self.samplerate, self.channels = self._probe_params()

    def run(self):
        try:
            samplerate, channels = self.samplerate, self.channels
            with sf.SoundFile(self.filepath, mode='w', samplerate=samplerate, channels=channels) as f_wav:
                with self._open_stream(samplerate=samplerate, channels=channels) as mic:
                    while not self.stop_event.is_set():
                        data = mic.record(numframes=2048)
                        f_wav.write(data)
                        self.frames += len(data)
        except Exception as e:
            # an unplugged device ends up here, the WAV keeps what was written
            self.error = str(e) or type(e).__name__

    def stop(self):
        self.stop_event.set()
        self.join()

# Application capture runs through proc-tap in a child process. The capture
# format is fixed, so these double as the recorder's rate and channel count.
PROC_TAP_SAMPLERATE = 48000
PROC_TAP_CHANNELS = 2


def _pid_alive(pid):
    """True if the target process is still running."""
    try:
        import psutil

        return psutil.pid_exists(int(pid))
    except Exception:
        return True


def _decode_message(message):
    """Normalize a capture message to ("data", bytes) or ("error", reason, detail)."""
    if isinstance(message, tuple) and message:
        if message[0] == "error":
            return ("error", message[1], message[2])
        if message[0] == "data":
            return message
    return ("data", message)


def _process_tap_worker(pid, sender):
    """Runs native application capture outside the tray process.

    A fault inside the capture library would otherwise take the whole app down
    with it, so the work happens in a spawned child.
    """
    capture = None
    try:
        import proctap

        capture = proctap.ProcessAudioCapture(pid)
        capture.start()
        sender.send(("ready", time.monotonic()))
        while True:
            data = capture.read(timeout=0.1)
            if data:
                # Timestamp before pipe delivery: a stalled parent must not
                # turn buffered audio into extra silence in the output.
                sender.send(("data", data, time.monotonic()))
    except Exception as e:
        # Report the real failure. Dying silently would look like the user's
        # application had simply closed.
        try:
            reason = "failed" if _pid_alive(pid) else "closed"
            sender.send(("error", reason, str(e) or type(e).__name__))
        except Exception:
            pass
    finally:
        if capture is not None:
            try:
                capture.stop()
                capture.close()
            except Exception:
                pass


class ProcTapRecorder(threading.Thread):
    """Records one application's audio to a WAV file.

    The child streams float32 buffers back over a pipe and this thread writes
    them out, so the mixing stage opens the result like any other track.
    """
    samplerate = PROC_TAP_SAMPLERATE
    channels = PROC_TAP_CHANNELS

    def __init__(self, pid, filepath, name=None):
        super().__init__()
        self.pid = pid
        self.filepath = filepath
        self.device = types.SimpleNamespace(name=name or f"application {pid}")
        self.stop_event = threading.Event()
        self.error = None
        self.exit_reason = None
        self.chunks_read = 0
        self.frames = 0
        self.ready_event = threading.Event()
        self.session_started: threading.Event | None = None
        self.session_epoch: float | None = None

    def probe(self):
        """Matches RawRecorder's interface; the capture format is fixed."""

    def run(self):
        import multiprocessing

        process = None
        receiver = None
        started_at = time.monotonic()
        try:
            receiver, sender = multiprocessing.Pipe(duplex=False)
            context = multiprocessing.get_context("spawn")
            process = context.Process(target=_process_tap_worker, args=(self.pid, sender), daemon=True)
            process.start()
            sender.close()
            with sf.SoundFile(self.filepath, mode="w", samplerate=self.samplerate,
                              channels=self.channels) as f_wav:
                # Pipe timeouts are not source silence. Leave gaps provisional
                # until a timestamped packet (or stop) establishes their extent.
                while not self.stop_event.is_set():
                    if receiver.poll(0.1):
                        incoming = receiver.recv()
                        if isinstance(incoming, tuple) and incoming[0] == "ready":
                            started_at = incoming[1]
                            self.ready_event.set()
                            if self.session_started is not None:
                                while not self.session_started.wait(0.05):
                                    if self.stop_event.is_set():
                                        return
                                assert self.session_epoch is not None
                                started_at = self.session_epoch
                            continue
                        message = _decode_message(incoming)
                        if message[0] == "error":
                            self.exit_reason = message[1]
                            raise RuntimeError(message[2])
                        data = np.frombuffer(message[1], dtype=np.float32).reshape(-1, self.channels)
                        if len(message) > 2:
                            packet_end = round((message[2] - started_at) * self.samplerate)
                            packet_start = packet_end - len(data)
                            if packet_end <= 0:
                                continue  # Captured during preparation, before the session.
                            if packet_start < 0:
                                data = data[-packet_start:]
                                packet_start = 0
                            # Worker timestamps approximate native packet time;
                            # never collapse a short quiet gap into later audio.
                            self._write_silence_until(f_wav, packet_start)
                        f_wav.write(data)
                        self.frames += len(data)
                        self.chunks_read += 1
                    elif process is not None and not process.is_alive():
                        if _pid_alive(self.pid):
                            self.exit_reason = "failed"
                            raise RuntimeError("Audio capture stopped unexpectedly.")
                        self.exit_reason = "closed"
                        raise RuntimeError("The application stopped or exited.")
                self._write_silence_until(
                    f_wav, round((time.monotonic() - started_at) * self.samplerate))
        except Exception as e:
            self.error = str(e)
        finally:
            if receiver is not None:
                try:
                    receiver.close()
                except Exception:
                    pass
            if process is not None:
                process.terminate()
                process.join(timeout=1.0)

    def _write_silence_until(self, output, frame):
        """Fill a known timeline gap with bounded-size blocks."""
        silence = np.zeros((4800, self.channels), dtype=np.float32)
        while self.frames < frame:
            count = min(len(silence), frame - self.frames)
            output.write(silence[:count])
            self.frames += count

    def stop(self):
        self.stop_event.set()
        self.join()


class AudioRecorder(threading.Thread):
    """
    Orchestrates recording from Microphone, Loopback, or Both.
    """
    def __init__(self, mic_id=None, source_mode=None, output_folder=None, output_format="mp3",
                 normalize=False, mono=False, on_finish_callback=None, on_progress=None,
                 on_notice=None, tracks=None, output_mode="mixed", on_started=None):
        super().__init__()
        self.mic_id = mic_id
        self.source_mode = source_mode # "mic", "loopback", "both"
        self.output_folder = output_folder
        self.output_format = output_format.lower()
        self.normalize = normalize
        self.mono = mono
        self.callback = on_finish_callback
        self.on_progress = on_progress  # called with 0..1 while saving
        self.on_notice = on_notice  # called with a message if a device is lost
        self.on_started = on_started

        # The track list is the source of truth. mic_id/source_mode are the
        # older single-source interface and are mapped onto a matching list.
        self.tracks = normalize_tracks(tracks if tracks is not None
                                       else tracks_from_mode(mic_id, source_mode))
        self.output_mode = normalize_output_mode(output_mode)

        self.recording = False
        self.stop_event = threading.Event()
        self.error_message = None
        self.final_filepath = None
        self.final_filepaths = []
        self.track_labels = []

        # Temp files
        self.temp_files = []
        self.recorders = []

    def _get_loopback_for(self, speaker_id=None):
        """The loopback device that mirrors the given (or default) speaker."""
        if speaker_id:
            speakers = sc.all_speakers()
            speaker = next((s for s in speakers if s.id == speaker_id), None)
            if speaker is None:
                raise Exception(
                    f"Speaker '{speaker_id}' not found. "
                    f"Available: {[s.name for s in speakers]}"
                )
        else:
            speaker = sc.default_speaker()

        mics = sc.all_microphones(include_loopback=True)
        loopback_mic = next((m for m in mics if m.name == speaker.name), None)
        if not loopback_mic:
            loopback_mic = next((m for m in mics if speaker.name in m.name), None)
        if not loopback_mic:
            available = [m.name for m in mics if getattr(m, 'isloopback', False)]
            raise Exception(
                f"No loopback device found for speaker '{speaker.name}'. "
                f"Available loopback devices: {available}"
            )
        return loopback_mic

    def _get_input_device(self, input_id=None):
        try:
            return sc.get_microphone(input_id, include_loopback=False)
        except Exception as e:
            available = [m.name for m in sc.all_microphones()]
            raise Exception(
                f"Microphone '{input_id}' not found ({e}). "
                f"Available: {available}"
            ) from e

    def _get_track_device(self, track):
        if track["kind"] == TRACK_KIND_INPUT:
            return self._get_input_device(track.get("input_id"))
        if track["kind"] == TRACK_KIND_OUTPUT:
            return self._get_loopback_for(track.get("speaker_id"))
        raise ValueError("Application tracks are captured by ProcTapRecorder")

    def _get_device(self, is_loopback):
        """Legacy helper for the older mic/loopback mode interface."""
        if is_loopback:
            return self._get_loopback_for()
        return self._get_input_device(self.mic_id)

    def run(self):
        self.recording = True
        self.error_message = None
        self.temp_files = []
        self.recorders = []
        self.track_labels = []
        self.final_filepaths = []

        try:
            # 1. Setup Recorders - one per configured track. Labels get a
            # number when a kind repeats, so two tracks never share a filename.
            kind_counts = {}
            for track in self.tracks:
                kind_counts[track["kind"]] = kind_counts.get(track["kind"], 0) + 1
            seen = {}
            for track in self.tracks:
                label = TRACK_LABELS.get(track["kind"], "track")
                if kind_counts[track["kind"]] > 1:
                    seen[label] = seen.get(label, 0) + 1
                    label = "%s%d" % (label, seen[label])
                self.track_labels.append(label)

                temp_path = tempfile.NamedTemporaryFile(suffix=".wav", delete=False).name
                self.temp_files.append(temp_path)
                if track["kind"] == TRACK_KIND_APP:
                    # proc-tap includes descendants of the selected process.
                    # Walking to an OS/launcher ancestor captures unrelated apps.
                    self.recorders.append(ProcTapRecorder(
                        track.get("target_pid"), temp_path,
                        name=track.get("target_name") or None))
                else:
                    device = self._get_track_device(track)
                    self.recorders.append(RawRecorder(device, temp_path))

            # Probe before starting so both sources start close together.
            # The loopback gets asked for the mic's rate first, so mixing
            # rarely has to resample.
            for r in self.recorders:
                if isinstance(r, ProcTapRecorder):
                    continue  # application capture has a fixed format
                if r is not self.recorders[0]:
                    r.samplerate = self.recorders[0].samplerate
                try:
                    r.probe()
                except Exception as e:
                    raise Exception(f"Recording failed on '{r.device.name}': {e}") from e

            print(f"Starting recording with {len(self.recorders)} track(s): "
                  f"{', '.join(self.track_labels)}")

            # Initialize native application capture before starting hardware or
            # announcing recording. Spawn/import latency is preparation, not
            # silent audio at the beginning of the session.
            session_started = threading.Event()
            for r in self.recorders:
                if isinstance(r, ProcTapRecorder):
                    r.session_started = session_started
                    r.start()
            for r in self.recorders:
                ready = getattr(r, "ready_event", None)
                if ready is None:
                    continue
                deadline = time.monotonic() + 15
                while not ready.wait(0.05):
                    if self.stop_event.is_set():
                        return
                    if not r.is_alive():
                        break  # Preserve the other tracks; report this failure on save.
                    if time.monotonic() >= deadline:
                        raise RuntimeError("Application capture did not become ready.")
            if self.stop_event.is_set():
                return
            epoch = time.monotonic()
            for r in self.recorders:
                if isinstance(r, ProcTapRecorder):
                    r.session_epoch = epoch
            session_started.set()
            for r in self.recorders:
                if not isinstance(r, ProcTapRecorder):
                    r.start()
            if self.on_started:
                self.on_started()

            # 3. Wait for the stop signal. A recorder only ends on its own when
            # its device is lost, then stop and save what was captured so far.
            while not self.stop_event.wait(0.5):
                lost = next((r for r in self.recorders if not r.is_alive()), None)
                if lost:
                    if self.on_notice:
                        self.on_notice(f"Lost '{lost.device.name}', saving recording...")
                    self.stop_event.set()

            # 4. Stop Recording. Fails only if nothing was captured at all.
            for r in self.recorders:
                r.stop()
            failed = next((r for r in self.recorders if r.error), None)
            if failed and not any(r.frames for r in self.recorders):
                raise Exception(f"Recording failed on '{failed.device.name}': {failed.error}")

            # 5. Mix, normalize and encode block by block. Loading a long
            # recording at once needs several GB of RAM (see #7).
            if not os.path.exists(self.output_folder):
                os.makedirs(self.output_folder)

            timestamp = time.strftime("%Y%m%d_%H%M%S")

            # A track that captured nothing usable - a failed application
            # capture, for instance - must not cost us the tracks that worked.
            usable = []
            for temp_path, label in zip(self.temp_files, self.track_labels):
                try:
                    if os.path.getsize(temp_path) > 0:
                        usable.append((sf.SoundFile(temp_path), label))
                except Exception:
                    continue
            if not usable:
                raise Exception("No audio was captured from any track")

            sources = [source for source, _ in usable]
            labels = [label for _, label in usable]

            try:
                if self.output_mode == "separate_files" and len(sources) > 1:
                    # Keep one file per track, all at the shared mix rate so
                    # they describe the same span and can be re-mixed later.
                    mix_rate, _, mix_length, ratios = self._plan_mix(sources)
                    self.final_filepaths = []
                    for source, label, ratio in zip(sources, labels, ratios):
                        path = os.path.join(
                            self.output_folder,
                            f"Recording_{timestamp}_{label}.{self.output_format}")
                        self._write_output([source], path, samplerate=mix_rate,
                                           mix_plan=(mix_rate, source.channels, mix_length, [ratio]))
                        self.final_filepaths.append(path)
                        # Keep earlier outputs visible if a later encoder fails.
                        self.final_filepath = list(self.final_filepaths)
                else:
                    path = os.path.join(
                        self.output_folder,
                        f"Recording_{timestamp}.{self.output_format}")
                    self._write_output(sources, path)
                    self.final_filepaths = [path]
                    self.final_filepath = path
            finally:
                for s in sources:
                    s.close()

            # saved, but shorter than asked for: the GUI keeps the file and warns
            if failed:
                self.error_message = f"Recording on '{failed.device.name}' stopped early: {failed.error}"

        except Exception as e:
            self.error_message = str(e)
            print(f"Error during recording process: {e}")
        finally:
            self.recording = False
            for recorder in self.recorders:
                if recorder.is_alive():
                    recorder.stop()
            # Clean up all temp files
            for t in self.temp_files:
                if os.path.exists(t):
                    try:
                        os.remove(t)
                    except: pass

            if self.callback:
                self.callback(self.final_filepath, self.error_message)

    def stop(self):
        self.stop_event.set()

    def _plan_mix(self, sources, samplerate=None):
        """Output rate, channels and length, plus a read ratio per source.

        The first source (the mic in "both" mode) sets the rate. A source
        that is off by less than 0.5% gets stretched to the full length,
        which fixes clock drift between devices on long recordings instead
        of letting mic and system audio slowly drift apart.
        """
        samplerate = samplerate or sources[0].samplerate
        channels = max(s.channels for s in sources)
        lengths = [s.frames * samplerate / s.samplerate for s in sources]
        length = int(round(max(lengths)))

        ratios = []
        for s, n in zip(sources, lengths):
            if length and abs(n - length) / length < 0.005:
                ratios.append(s.frames / length)
            else:
                ratios.append(s.samplerate / samplerate)
        return samplerate, channels, length, ratios

    @staticmethod
    def _read_block(source, start, count, ratio):
        """Reads `count` output frames starting at output frame `start`."""
        if ratio == 1:
            source.seek(min(start, source.frames))
            data = source.read(count, dtype='float32', always_2d=True)
        else:
            pos = (start + np.arange(count)) * ratio
            lo = min(int(pos[0]), source.frames)
            hi = min(int(pos[-1]) + 2, source.frames)
            source.seek(lo)
            chunk = source.read(hi - lo, dtype='float32', always_2d=True)
            data = np.zeros((count, source.channels), dtype='float32')
            inside = pos <= hi - 1
            if len(chunk) and inside.any():
                idx = np.arange(lo, hi)
                for c in range(source.channels):
                    data[inside, c] = np.interp(pos[inside], idx, chunk[:, c])
            return data

        if len(data) < count:
            data = np.concatenate((data, np.zeros((count - len(data), data.shape[1]), dtype='float32')))
        return data

    def _mixed_blocks(self, sources, gains=None, block=65536, samplerate=None, mix_plan=None):
        samplerate, channels, length, ratios = mix_plan or self._plan_mix(sources, samplerate)
        gains = gains or [1.0] * len(sources)
        for start in range(0, length, block):
            count = min(block, length - start)
            mixed = np.zeros((count, channels), dtype='float32')
            for s, ratio, g in zip(sources, ratios, gains):
                # mono into stereo: same signal on both sides
                mixed += g * self._read_block(s, start, count, ratio)
            yield np.clip(mixed, -1.0, 1.0)

    def _write_output(self, sources, path, samplerate=None, mix_plan=None):
        samplerate, channels, length, _ = mix_plan or self._plan_mix(sources, samplerate)
        per_source = self.normalize and len(sources) > 1

        # progress over all passes: per-source peaks, mix peak, final write.
        # Encoding is far slower than scanning for peaks, so it counts more
        # (measured on a 30 min recording: mp3 ~25x, wav/flac ~3x a scan)
        write_cost = 25 if self.output_format == "mp3" else 3
        total = length * write_cost
        if self.normalize:
            total += length
        if per_source:
            total += sum(s.frames for s in sources)
        done = 0

        def tick(b, cost=1):
            nonlocal done
            done += len(b) * cost
            if self.on_progress and total:
                self.on_progress(min(done / total, 1.0))
            return b

        source_gains = None
        if per_source:
            # Level mic and system audio first, otherwise loud system audio
            # sets the peak and a quiet mic stays quiet in the mix
            source_gains = []
            for s in sources:
                s.seek(0)
                peak = max((np.abs(tick(b)).max() for b in s.blocks(65536, dtype='float32')), default=0)
                source_gains.append(1.0 / peak if peak > 0 else 1.0)

        blocks = lambda: self._mixed_blocks(sources, source_gains, samplerate=samplerate, mix_plan=mix_plan)
        if self.mono and channels > 1:
            channels = 1
            blocks = lambda: (b.mean(axis=1, keepdims=True) for b in self._mixed_blocks(sources, source_gains, samplerate=samplerate, mix_plan=mix_plan))

        gain = 1.0
        if self.normalize:
            peak = max((np.abs(tick(b)).max() for b in blocks()), default=0)
            if peak > 0:
                gain = 0.99 / peak

        if self.output_format == "mp3":
            encoder = lameenc.Encoder()
            encoder.set_bit_rate(192)
            encoder.set_in_sample_rate(samplerate)
            encoder.set_channels(channels)
            encoder.set_quality(2)
            with open(path, "wb") as f_mp3:
                for b in blocks():
                    tick(b, write_cost)
                    pcm = (b * gain * 32767).clip(-32768, 32767).astype(np.int16)
                    f_mp3.write(encoder.encode(pcm.tobytes()))
                f_mp3.write(encoder.flush())
        else:
            # The temporary output path may not have the final extension.
            output_format = "FLAC" if self.output_format == "flac" else "WAV"
            subtype = "PCM_16" if self.output_format == "wav" else None
            with sf.SoundFile(path, 'w', samplerate=samplerate, channels=channels,
                              format=output_format, subtype=subtype) as f_out:
                for b in blocks():
                    f_out.write(tick(b, write_cost) * gain)

def get_speakers():
    """Speakers available for system-audio tracks, as id/name pairs."""
    try:
        return [{"id": s.id, "name": s.name} for s in sc.all_speakers()]
    except Exception as e:
        print(f"Error fetching speakers: {e}")
        return []


def get_devices(include_loopback=False):
    try:
        devices = sc.all_microphones(include_loopback=include_loopback)
        return [{"id": d.id, "name": d.name} for d in devices]
    except Exception as e:
        print(f"Error fetching devices: {e}")
        return []
