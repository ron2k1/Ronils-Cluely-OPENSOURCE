"""Live listening: captures the call (system audio via WASAPI loopback) AND your
own mic, transcribes both locally with faster-whisper, and emits labelled lines.

Loopback hears the *other* participants (whatever your speakers play); the mic
hears *you*. Each line is tagged "Them" or "You" so the rest of the app knows who
spoke — e.g. only the other side's questions should trigger an auto-answer.

Capture runs as two producer threads (one per device) feeding a queue; a single
consumer runs whisper, so there is never a concurrent transcribe on one model.
"""
import os
import queue
import threading
import time

import numpy as np
from PySide6.QtCore import QThread, Signal

from config import (
    CAPTURE_MIC, CAPTURE_SYSTEM, MIC_DEVICE, SPEAKER_DEVICE,
    WHISPER_COMPUTE, WHISPER_DEVICE, WHISPER_MODEL,
)

SAMPLE_RATE = 16000
# Shorter chunk = a voiced question reaches whisper sooner (lower pickup latency).
# 2s is the sweet spot for base.en on CPU; raise for a bit more accuracy.
CHUNK_SECONDS = float(os.environ.get("CLUELY_CHUNK_SECONDS", "2.0"))
SUB_SECONDS = 0.5         # capture in small blocks so stop reacts in ~0.5s
RMS_GATE = 0.006          # skip near-silent chunks (whisper hallucinates on silence)
_QUEUE_MAX = 8            # ~32s buffered before we drop oldest to stay real-time


def _add_cuda_dlls():
    """Put pip-installed CUDA libs on the DLL search path (GPU mode only)."""
    try:
        import importlib.util
        import pathlib
        spec = importlib.util.find_spec("nvidia")
        if not spec or not spec.submodule_search_locations:
            return
        base = pathlib.Path(spec.submodule_search_locations[0])
        for sub in ("cublas/bin", "cudnn/bin", "cuda_nvrtc/bin"):
            d = base / sub
            if d.is_dir():
                os.add_dll_directory(str(d))
    except Exception:
        pass


def list_output_devices():
    """Enumerate render endpoints for the UI 'Call audio' picker.

    Returns [(name, is_default), …]; an empty list if enumeration fails (the UI
    then just offers the auto/default option). WASAPI loopback is endpoint-
    specific, so the user must pick the SAME output device Zoom plays the call
    through — see _open_loopback / the README troubleshooting note.
    """
    try:
        import soundcard as sc
        default = ""
        try:
            default = sc.default_speaker().name
        except Exception:
            pass
        return [(spk.name, spk.name == default) for spk in sc.all_speakers()]
    except Exception:
        return []


class Listener(QThread):
    line = Signal(str, str)   # (text, source); source in {"Them", "You"}
    status = Signal(str)      # human-readable state

    def __init__(self, speaker_device=None):
        super().__init__()
        self._stop = threading.Event()
        self._model = None
        self._q: queue.Queue = queue.Queue(maxsize=_QUEUE_MAX)
        self._cap_threads: list[threading.Thread] = []
        # Explicit loopback endpoint chosen in the UI ("" / None = system default).
        self._speaker_device = speaker_device
        self._loopback_name = ""
        # Per-source count of chunks that passed the RMS gate. Used to detect the
        # silent-loopback (wrong-endpoint) failure: mic active but call dead.
        self._passed = {"Them": 0, "You": 0}
        self._warned_silent = False

    def stop(self):
        self._stop.set()

    def _load_model(self):
        from faster_whisper import WhisperModel
        if WHISPER_DEVICE == "cuda":
            _add_cuda_dlls()
            try:
                self.status.emit(f"loading whisper ({WHISPER_MODEL}, cuda)…")
                return WhisperModel(WHISPER_MODEL, device="cuda", compute_type=WHISPER_COMPUTE)
            except Exception as exc:
                self.status.emit(f"GPU unavailable ({type(exc).__name__}); using CPU")
        self.status.emit(f"loading whisper ({WHISPER_MODEL}, cpu)…")
        return WhisperModel(WHISPER_MODEL, device="cpu", compute_type="int8")

    def _open_loopback(self, sc):
        name = self._loopback_name or self._speaker_device or SPEAKER_DEVICE \
            or sc.default_speaker().name
        return sc.get_microphone(str(name), include_loopback=True)

    def _open_mic(self, sc):
        if MIC_DEVICE:
            return sc.get_microphone(str(MIC_DEVICE), include_loopback=False)
        return sc.default_microphone()

    def _capture(self, source, opener):
        """Producer: record one device in small sub-blocks, accumulate to a full
        chunk, gate on RMS, push (source, audio) onto the queue. Device errors are
        per-source, so one dead device never silences the other."""
        import soundcard as sc
        try:
            dev = opener(sc)
        except Exception as exc:
            self.status.emit(f"{source} audio unavailable ({type(exc).__name__})")
            return
        sub = int(SAMPLE_RATE * SUB_SECONDS)
        full = int(SAMPLE_RATE * CHUNK_SECONDS)
        try:
            with dev.recorder(samplerate=SAMPLE_RATE, channels=1) as rec:
                buf: list = []
                have = 0
                while not self._stop.is_set():
                    block = np.asarray(rec.record(numframes=sub), dtype=np.float32).reshape(-1)
                    if block.size:
                        buf.append(block)
                        have += block.size
                    if have < full:
                        continue
                    audio = np.concatenate(buf)
                    # Gate on the LOUDEST 0.5s sub-block, not the whole-window
                    # mean. A brief or quiet utterance embedded in silence (a
                    # short "yes?", a soft-spoken question over VoIP) averages
                    # below the gate across the full 2s window and would vanish,
                    # even though the speech itself clears the threshold. Peak-
                    # gating keeps it while still rejecting fully-silent windows.
                    peak = max((float(np.sqrt(np.mean(b * b))) for b in buf),
                               default=0.0)
                    buf, have = [], 0
                    if peak < RMS_GATE:
                        continue
                    self._passed[source] += 1
                    try:
                        self._q.put_nowait((source, audio))
                    except queue.Full:
                        try:
                            self._q.get_nowait()              # drop oldest, keep recent
                            self._q.put_nowait((source, audio))
                        except (queue.Empty, queue.Full):
                            pass  # raced with the other producer; just drop this chunk
        except Exception as exc:
            self.status.emit(f"{source} listen error: {type(exc).__name__}")

    def run(self):
        try:
            self._model = self._load_model()
        except Exception as exc:
            self.status.emit(f"whisper load error: {exc}")
            return

        # Resolve the loopback endpoint name ONCE so the producer opens a fixed
        # device and the status line can show the user exactly what's captured.
        if CAPTURE_SYSTEM:
            try:
                import soundcard as sc
                self._loopback_name = (
                    self._speaker_device or SPEAKER_DEVICE or sc.default_speaker().name
                )
            except Exception:
                self._loopback_name = ""

        sources = []
        if CAPTURE_SYSTEM:
            sources.append(("Them", self._open_loopback))
        if CAPTURE_MIC:
            sources.append(("You", self._open_mic))
        if not sources:
            self.status.emit("no audio sources enabled")
            return

        for src, opener in sources:
            t = threading.Thread(target=self._capture, args=(src, opener), daemon=True)
            t.start()
            self._cap_threads.append(t)

        label = "listening (you + call)" if len(sources) == 2 else "listening"
        if CAPTURE_SYSTEM and self._loopback_name:
            label += f" · call: {self._loopback_name}"
        self.status.emit(label)

        # Consumer: serialize whisper across both sources (one model, no races).
        listen_start = time.monotonic()
        while not self._stop.is_set():
            # Wrong-endpoint detector: if the mic is clearly active (a real
            # conversation) but the loopback has produced ZERO audio for a while,
            # Cluely is almost certainly listening to the wrong speaker — the
            # exact silent failure where Zoom's output device differs from this
            # one. Keying off "mic alive, call dead" avoids the false positive a
            # naive startup RMS probe would hit during legitimate silence.
            if (not self._warned_silent and CAPTURE_SYSTEM and CAPTURE_MIC
                    and self._passed["You"] >= 2 and self._passed["Them"] == 0
                    and time.monotonic() - listen_start > 25):
                self._warned_silent = True
                self.status.emit("⚠ no call audio — set 'Call audio' to Zoom's speaker")
            try:
                source, audio = self._q.get(timeout=0.3)
            except queue.Empty:
                continue
            try:
                segments, _ = self._model.transcribe(
                    audio, language="en", beam_size=1, vad_filter=True
                )
                text = " ".join(s.text.strip() for s in segments).strip()
            except Exception:
                continue
            if text:
                self.line.emit(text, source)

        for t in self._cap_threads:
            t.join(timeout=2.0)
        self.status.emit("stopped")
