import soundcard as sc
import soundfile as sf
import contextlib
import queue
import types
import threading
import time
import os
import lameenc
import numpy as np
import tempfile

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
                    with self.device.recorder(samplerate=sr, channels=ch):
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
                with self.device.recorder(samplerate=samplerate, channels=channels) as mic:
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

class AudioRecorder(threading.Thread):
    """
    Orchestrates recording from Microphone, Loopback, or Both.
    """
    def __init__(self, mic_id, source_mode, output_folder, output_format="mp3", 
                 normalize=False, mono=False, on_finish_callback=None, on_progress=None,
                 on_notice=None):
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
        
        self.recording = False
        self.stop_event = threading.Event()
        self.error_message = None
        self.final_filepath = None
        
        # Temp files
        self.temp_files = []
        self.recorders = []

    def _get_device(self, is_loopback):
        if is_loopback:
            default_speaker = sc.default_speaker()
            mics = sc.all_microphones(include_loopback=True)
            loopback_mic = next((m for m in mics if m.name == default_speaker.name), None)
            if not loopback_mic:
                loopback_mic = next((m for m in mics if default_speaker.name in m.name), None)
            if not loopback_mic:
                available = [m.name for m in mics if m.isloopback] if any(hasattr(m, 'isloopback') for m in mics) else [m.name for m in mics]
                raise Exception(
                    f"No loopback device found for speaker '{default_speaker.name}'. "
                    f"Available loopback devices: {available}"
                )
            return loopback_mic
        else:
            try:
                return sc.get_microphone(self.mic_id, include_loopback=False)
            except Exception as e:
                available = [m.name for m in sc.all_microphones()]
                raise Exception(
                    f"Microphone '{self.mic_id}' not found ({e}). "
                    f"Available: {available}"
                ) from e

    def run(self):
        self.recording = True
        self.error_message = None
        self.temp_files = []
        self.recorders = []
        
        try:
            # 1. Setup Recorders
            if self.source_mode == "both":
                # Need two recorders
                dev_mic = self._get_device(is_loopback=False)
                dev_loop = self._get_device(is_loopback=True)
                
                t1 = tempfile.NamedTemporaryFile(suffix=".wav", delete=False).name
                t2 = tempfile.NamedTemporaryFile(suffix=".wav", delete=False).name
                self.temp_files = [t1, t2]
                
                self.recorders.append(RawRecorder(dev_mic, t1))
                self.recorders.append(RawRecorder(dev_loop, t2))
                
            elif self.source_mode == "loopback":
                dev = self._get_device(is_loopback=True)
                t1 = tempfile.NamedTemporaryFile(suffix=".wav", delete=False).name
                self.temp_files = [t1]
                self.recorders.append(RawRecorder(dev, t1))
                
            else: # mic
                dev = self._get_device(is_loopback=False)
                t1 = tempfile.NamedTemporaryFile(suffix=".wav", delete=False).name
                self.temp_files = [t1]
                self.recorders.append(RawRecorder(dev, t1))

            # Probe before starting so both sources start close together.
            # The loopback gets asked for the mic's rate first, so mixing
            # rarely has to resample.
            for r in self.recorders:
                if r is not self.recorders[0]:
                    r.samplerate = self.recorders[0].samplerate
                try:
                    r.probe()
                except Exception as e:
                    raise Exception(f"Recording failed on '{r.device.name}': {e}") from e

            print(f"Starting recording mode: {self.source_mode}")

            # 2. Start Recording
            for r in self.recorders:
                r.start()
            
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
            filename = f"Recording_{timestamp}.{self.output_format}"
            self.final_filepath = os.path.join(self.output_folder, filename)

            sources = [sf.SoundFile(t) for t in self.temp_files]
            try:
                self._write_output(sources, self.final_filepath)
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

    def _plan_mix(self, sources):
        """Output rate, channels and length, plus a read ratio per source.

        The first source (the mic in "both" mode) sets the rate. A source
        that is off by less than 0.5% gets stretched to the full length,
        which fixes clock drift between devices on long recordings instead
        of letting mic and system audio slowly drift apart.
        """
        samplerate = sources[0].samplerate
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

    def _mixed_blocks(self, sources, gains=None, block=65536):
        samplerate, channels, length, ratios = self._plan_mix(sources)
        gains = gains or [1.0] * len(sources)
        for start in range(0, length, block):
            count = min(block, length - start)
            mixed = np.zeros((count, channels), dtype='float32')
            for s, ratio, g in zip(sources, ratios, gains):
                # mono into stereo: same signal on both sides
                mixed += g * self._read_block(s, start, count, ratio)
            yield np.clip(mixed, -1.0, 1.0)

    def _write_output(self, sources, path):
        samplerate, channels, length, _ = self._plan_mix(sources)
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

        blocks = lambda: self._mixed_blocks(sources, source_gains)
        if self.mono and channels > 1:
            channels = 1
            blocks = lambda: (b.mean(axis=1, keepdims=True) for b in self._mixed_blocks(sources, source_gains))

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
            # WAV or FLAC, soundfile picks the container from the extension
            with sf.SoundFile(path, 'w', samplerate=samplerate, channels=channels) as f_out:
                for b in blocks():
                    f_out.write(tick(b, write_cost) * gain)

def get_devices(include_loopback=False):
    try:
        devices = sc.all_microphones(include_loopback=include_loopback)
        return [{"id": d.id, "name": d.name} for d in devices]
    except Exception as e:
        print(f"Error fetching devices: {e}")
        return []
