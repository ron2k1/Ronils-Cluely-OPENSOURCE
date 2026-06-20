"""Cluely overlay: a frameless, always-on-top panel excluded from screen
capture (SetWindowDisplayAffinity). Live mode continuously listens to the call,
transcribes locally, and auto-answers questions aimed at you.
"""
import base64
import ctypes
import time
from difflib import SequenceMatcher

from PySide6.QtCore import QBuffer, QByteArray, QEvent, Qt, Signal, QThread
from PySide6.QtGui import QColor, QImage, QKeySequence, QPainter
from PySide6.QtWidgets import (
    QApplication, QCheckBox, QComboBox, QFileDialog, QHBoxLayout, QLabel,
    QLineEdit, QPushButton, QSizeGrip, QTabWidget, QTextBrowser, QTextEdit,
    QVBoxLayout, QWidget,
)

import brain
from config import (
    APP_NAME, AUTO_FROM, AUTO_MIN_NEWCHARS, AUTO_THROTTLE_S, ECHO_GUARD,
    ECHO_RATIO, ECHO_WINDOW_S, MY_NAME, NOTES_DIR, OPACITY, VISION_MONITOR,
)
from ears import Listener, list_output_devices

# Win32 constants
WDA_NONE = 0x00000000
WDA_EXCLUDEFROMCAPTURE = 0x00000011
GWL_EXSTYLE = -20
WS_EX_TRANSPARENT = 0x00000020
WS_EX_LAYERED = 0x00080000

user32 = ctypes.windll.user32
user32.SetWindowDisplayAffinity.argtypes = [ctypes.c_void_p, ctypes.c_uint]
user32.SetWindowDisplayAffinity.restype = ctypes.c_bool
user32.GetWindowLongW.restype = ctypes.c_long
user32.SetWindowLongW.restype = ctypes.c_long

AUTO_PROMPT = (
    "In this live meeting I may have just been asked something. From the recent "
    "transcript, give me a crisp answer I can say out loud right now (1-3 sentences, "
    "no preamble). If no question was actually directed at me, instead give a single "
    "short bullet noting the key point just discussed."
)
QUESTION_HINTS = (
    "?", "what do you", "what's your", "your thoughts", "thoughts on", "how would you",
    "can you", "could you", "do you have", "any questions", "over to you",
    "what about you", "your take", "tell me", "tell us", "walk me", "walk us",
    "explain", "describe", "give us", "give me", "how do", "how does", "why ",
)
# If you set CLUELY_MY_NAME, treat a line that says your name as aimed at you
# ("Ada, what do you think?"). Each name word is matched, lowercased.
if MY_NAME:
    QUESTION_HINTS = QUESTION_HINTS + tuple(
        w for w in MY_NAME.lower().split() if len(w) >= 2
    )
# Whisper often drops the "?", so also treat an utterance that OPENS with a
# question/imperative word as a question — catches "tell me about…", "walk us
# through…", "how are you handling…" that match none of the fixed phrases above.
QUESTION_WORDS = frozenset((
    "what", "how", "why", "when", "where", "who", "which", "whose", "can", "could",
    "would", "will", "should", "do", "does", "did", "are", "is", "was", "were",
    "have", "has", "tell", "walk", "explain", "describe", "give", "talk",
))
VISION_PROMPT = (
    "This image is my current screen. If it shows a coding problem (e.g. LeetCode), "
    "respond with: "
    "(1) 'Problem (say this first):' — restate the problem in my own plain words as 1-2 "
    "natural first-person sentences I can say out loud to show I understood what's being "
    "asked: what I'm given (inputs), what I have to return (output), and the key "
    "constraint or edge case. "
    "(2) the optimal approach in one line, (3) time and space complexity, "
    "(4) a complete, correct, runnable solution in the language shown (default Python), "
    "with a line number at the start of every line, "
    "(5) a 'Read aloud (line by line):' section keyed to those line numbers "
    "('Line 1: ...', 'Line 2: ...', covering every line) — one short, natural spoken "
    "sentence per line in a relaxed, conversational first-person voice (contractions, "
    "plain words, not robotic) that I can read out verbatim. "
    "If it is not a coding problem, concisely explain what is on screen and what I should "
    "say or do next. Keep it tight and glanceable."
)


def _qimage_to_b64(img) -> str:
    """Encode a QImage as a base64 PNG string."""
    ba = QByteArray()
    buf = QBuffer(ba)
    buf.open(QBuffer.OpenModeFlag.WriteOnly)
    img.save(buf, "PNG")
    return base64.b64encode(bytes(ba.data())).decode("ascii")


def _norm_line(text: str) -> str:
    """Lowercase + collapse whitespace so echo comparison ignores casing and the
    minor spacing differences two whisper passes produce on the same utterance."""
    return " ".join(text.lower().split())


class AskWorker(QThread):
    """Runs one query off the UI thread, streaming text back via signals."""
    chunk = Signal(str)
    finished_text = Signal(str)
    failed = Signal(str)

    def __init__(self, question, transcript, notes, image_b64="", fast=False):
        super().__init__()
        self._q, self._t, self._n = question, transcript, notes
        self._img = image_b64
        self._fast = fast

    def run(self):
        try:
            full = brain.ask(self._q, self._t, self._n,
                             on_text=lambda s: self.chunk.emit(s),
                             image_b64=self._img, fast=self._fast)
            self.finished_text.emit(full)
        except Exception as exc:
            self.failed.emit(f"{type(exc).__name__}: {exc}")


class Overlay(QWidget):
    sig_toggle = Signal()
    sig_focus_input = Signal()
    sig_toggle_clickthrough = Signal()
    sig_live = Signal()
    sig_vision = Signal()
    sig_image = Signal()
    sig_quit = Signal()

    def __init__(self):
        super().__init__()
        self._click_through = False
        self._drag_pos = None
        self._workers = []
        self._convo = []                 # list[ (role, text) ]
        self._transcript_buf = []
        self._live = False
        self._listener = None
        self._retiring = []              # listeners stopped but not yet finished
        self._last_auto = 0.0
        self._last_auto_len = 0
        self._auto_inflight = False
        self._pending_auto = None        # newest follow-up queued while answering
        self._recent_lines = []          # [(monotonic_ts, source, norm_text)] echo guard

        self.setWindowFlags(Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setWindowOpacity(OPACITY)
        self.resize(410, 560)

        self._build_ui()
        self._apply_style()
        self._set_power(False)
        self._position_top_right()

        self.sig_toggle.connect(self.toggle_visible)
        self.sig_focus_input.connect(self.focus_input)
        self.sig_toggle_clickthrough.connect(self.toggle_clickthrough)
        self.sig_live.connect(self.toggle_live)
        self.sig_vision.connect(self.ask_screen)
        self.sig_image.connect(self.attach_image)
        self.sig_quit.connect(self._quit)
        self.input.installEventFilter(self)

    # ---------- construction ----------
    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(12, 10, 12, 8)

        header = QHBoxLayout()
        self.title = QLabel(f"● {APP_NAME}")
        self.title.setObjectName("title")
        self.status = QLabel("starting…")
        self.status.setObjectName("status")
        self.btn_close = QPushButton("×")
        self.btn_close.setObjectName("close")
        self.btn_close.setFixedSize(24, 22)
        self.btn_close.setToolTip("Close Cluely (saves your notes)")
        self.btn_close.clicked.connect(self._quit)
        header.addWidget(self.title)
        header.addStretch()
        header.addWidget(self.status)
        header.addWidget(self.btn_close)
        root.addLayout(header)

        # live controls row
        ctl = QHBoxLayout()
        self.btn_live = QPushButton("⏻ OFF")
        self.btn_live.setObjectName("power")
        self.btn_live.setProperty("on", False)
        self.btn_live.setToolTip(
            "Turn Cluely listening + auto-answer on/off  (Ctrl+Alt+Space)")
        self.btn_live.clicked.connect(self.toggle_live)
        self.btn_vision = QPushButton("Solve screen")
        self.btn_vision.clicked.connect(self.ask_screen)
        self.chk_auto = QCheckBox("Auto-answer")
        self.chk_auto.setChecked(True)
        ctl.addWidget(self.btn_live)
        ctl.addWidget(self.btn_vision)
        ctl.addWidget(self.chk_auto)
        ctl.addStretch()
        root.addLayout(ctl)

        # Which speaker output Cluely loopbacks for the *call* audio. WASAPI
        # loopback is endpoint-specific, so this MUST match Zoom's own Speaker
        # setting or the other side is captured as pure silence (no "Them" lines,
        # no auto-answer) — the silent failure the listener now warns about.
        dev = QHBoxLayout()
        lbl = QLabel("Call audio:")
        lbl.setObjectName("status")
        self.cmb_speaker = QComboBox()
        self.cmb_speaker.setToolTip(
            "The speaker output Cluely listens to for the call.\n"
            "Must match Zoom > Settings > Audio > Speaker.")
        self._populate_speakers()
        self.cmb_speaker.currentIndexChanged.connect(self._on_speaker_changed)
        dev.addWidget(lbl)
        dev.addWidget(self.cmb_speaker, 1)
        root.addLayout(dev)

        self.tabs = QTabWidget()
        root.addWidget(self.tabs, 1)

        # Ask / answers
        ask = QWidget()
        av = QVBoxLayout(ask)
        av.setContentsMargins(0, 0, 0, 0)
        self.chat = QTextBrowser()
        self.chat.setOpenExternalLinks(True)
        av.addWidget(self.chat, 1)
        inrow = QHBoxLayout()
        self.input = QLineEdit()
        self.input.setPlaceholderText("Ask Claude…  (Enter, or paste an image)")
        self.input.returnPressed.connect(self.on_ask)
        self.btn_img = QPushButton("📎")
        self.btn_img.setFixedWidth(36)
        self.btn_img.setToolTip(
            "Attach image — uses a copied image if present, else pick a file")
        self.btn_img.clicked.connect(self.attach_image)
        inrow.addWidget(self.input, 1)
        inrow.addWidget(self.btn_img)
        av.addLayout(inrow)
        self.tabs.addTab(ask, "Answers")

        # Notes
        notes = QWidget()
        nv = QVBoxLayout(notes)
        nv.setContentsMargins(0, 0, 0, 0)
        self.notes = QTextEdit()
        self.notes.setPlaceholderText("Your notes (autosaved on exit)…")
        nv.addWidget(self.notes)
        self.tabs.addTab(notes, "Notes")

        # Transcript
        tr = QWidget()
        tv = QVBoxLayout(tr)
        tv.setContentsMargins(0, 0, 0, 0)
        self.transcript = QTextBrowser()
        tv.addWidget(self.transcript, 1)
        self.tabs.addTab(tr, "Transcript")

        root.addWidget(QSizeGrip(self), 0, Qt.AlignRight | Qt.AlignBottom)

    def _apply_style(self):
        self.setStyleSheet(
            """
            QWidget { color: #e6e6e6; font-family: 'Segoe UI'; font-size: 13px; }
            #title { font-weight: 600; color: #7dd3fc; }
            #status { color: #94a3b8; font-size: 11px; }
            QTabWidget::pane { border: 0; }
            QTabBar::tab { background: rgba(255,255,255,0.05); padding: 4px 12px;
                           margin-right: 3px; border-radius: 6px; }
            QTabBar::tab:selected { background: rgba(125,211,252,0.25); }
            QTextBrowser, QTextEdit, QLineEdit {
                background: rgba(15,18,24,0.85);
                border: 1px solid rgba(255,255,255,0.08);
                border-radius: 8px; padding: 6px; }
            QPushButton { background: rgba(125,211,252,0.20); border: 0;
                          border-radius: 8px; padding: 5px 12px; }
            QPushButton:hover { background: rgba(125,211,252,0.35); }
            QCheckBox { color: #cbd5e1; }
            QPushButton#power { font-weight: 600; }
            QPushButton#power[on="true"]  { background: rgba(34,197,94,0.30); color: #bbf7d0; }
            QPushButton#power[on="true"]:hover  { background: rgba(34,197,94,0.45); }
            QPushButton#power[on="false"] { background: rgba(148,163,184,0.18); color: #cbd5e1; }
            QPushButton#power[on="false"]:hover { background: rgba(148,163,184,0.30); }
            QPushButton#close { background: transparent; color: #94a3b8;
                                font-size: 15px; font-weight: 700; padding: 0; border-radius: 6px; }
            QPushButton#close:hover { background: rgba(239,68,68,0.40); color: #ffffff; }
            """
        )

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.setBrush(QColor(10, 12, 16, 225))
        p.setPen(Qt.NoPen)
        p.drawRoundedRect(self.rect(), 14, 14)

    def _position_top_right(self):
        geo = self.screen().availableGeometry()
        self.move(geo.right() - self.width() - 24, geo.top() + 24)

    # ---------- capture exclusion ----------
    def showEvent(self, e):
        super().showEvent(e)
        self._set_capture_exclusion(True)

    def _set_capture_exclusion(self, on):
        try:
            hwnd = int(self.winId())
            ok = user32.SetWindowDisplayAffinity(
                hwnd, WDA_EXCLUDEFROMCAPTURE if on else WDA_NONE
            )
            self._set_status("hidden from capture" if ok else "VISIBLE (failed)")
        except Exception:
            self._set_status("affinity error")

    def _set_status(self, msg):
        prefix = "LIVE · " if self._live else ""
        self.status.setText(prefix + msg)

    # ---------- dragging ----------
    def mousePressEvent(self, e):
        if e.button() == Qt.LeftButton and e.position().y() < 38:
            self._drag_pos = e.globalPosition().toPoint() - self.frameGeometry().topLeft()
            e.accept()

    def mouseMoveEvent(self, e):
        if self._drag_pos is not None and e.buttons() & Qt.LeftButton:
            self.move(e.globalPosition().toPoint() - self._drag_pos)
            e.accept()

    def mouseReleaseEvent(self, _):
        self._drag_pos = None

    # ---------- live mode ----------
    def toggle_live(self):
        if self._live:
            self._live = False
            self._retire_listener()
            self._set_power(False)
            self._set_status("stopped")
        else:
            self._live = True
            self._set_power(True)
            self._start_listener()

    def _set_power(self, on):
        """Reflect the live/idle state on the power switch: '⏻ LIVE' (green) when
        listening, '⏻ OFF' (grey) when idle. Re-polish so the dynamic [on=...]
        stylesheet selector repaints."""
        self.btn_live.setText("⏻ LIVE" if on else "⏻ OFF")
        self.btn_live.setProperty("on", on)
        self.btn_live.style().unpolish(self.btn_live)
        self.btn_live.style().polish(self.btn_live)

    def _start_listener(self):
        """Spawn a Listener bound to the currently-selected call-audio device."""
        self._listener = Listener(speaker_device=self._selected_speaker())
        self._listener.line.connect(self._on_transcript)
        self._listener.status.connect(self._set_status)
        self._listener.finished.connect(self._reap_listener)
        self._listener.start()

    def _populate_speakers(self):
        """Fill the call-audio picker with output endpoints. The data role holds
        the device name ('' = system default) so the listener can pin it."""
        self.cmb_speaker.addItem("Auto (system default)", "")
        for name, is_default in list_output_devices():
            self.cmb_speaker.addItem(f"{name}  ·  default" if is_default else name, name)

    def _selected_speaker(self):
        """The picked device name, or None for the system default."""
        return self.cmb_speaker.currentData() or None

    def _on_speaker_changed(self, _i):
        # Loopback endpoint is fixed at Listener spawn, so restart to apply it
        # live (mid-meeting device switch). No-op until the user goes live.
        if self._live:
            self._retire_listener()
            self._start_listener()

    def _retire_listener(self):
        """Tear down the active listener without blocking the UI.

        A running QThread must NOT be garbage-collected — Qt aborts the whole
        process with "QThread: Destroyed while thread is still running". So we
        (1) stop accepting its signals immediately, so no stale transcript line
        or ghost auto-answer bleeds in after the user toggled off; (2) hold a
        strong reference in self._retiring so the object cannot be collected
        while run() is still unwinding; and (3) let its own `finished` signal
        fire _reap_listener to drop that reference once the thread has actually
        terminated. We deliberately do not wait() here — that would freeze the
        UI for up to ~2.5s on every toggle.
        """
        lst = self._listener
        self._listener = None
        if not lst:
            return
        for sig, slot in ((lst.line, self._on_transcript),
                          (lst.status, self._set_status)):
            try:
                sig.disconnect(slot)
            except (TypeError, RuntimeError):
                pass  # already gone / never connected
        self._retiring.append(lst)
        lst.stop()

    def _reap_listener(self):
        """Drop our last reference to a listener once its thread has finished.

        Runs on the GUI thread (queued from the worker's `finished`), so mutating
        self._retiring needs no lock. Only now is destroying the QThread safe.
        """
        t = self.sender()
        if t in self._retiring:
            self._retiring.remove(t)

    def _on_transcript(self, text, source="Them"):
        now = time.monotonic()
        # Drop acoustic echo / mic-sidetone: a line that near-duplicates a recent
        # line from the OTHER source is the same utterance heard twice. The later
        # copy is the echo — discard it entirely (no log, no auto-trigger).
        if self._is_echo(text, source, now):
            return
        self._recent_lines.append((now, source, _norm_line(text)))
        self._recent_lines = [r for r in self._recent_lines if now - r[0] <= ECHO_WINDOW_S]

        labeled = f"{source}: {text}"
        self._transcript_buf.append(labeled)
        self.transcript.append(labeled)
        bar = self.transcript.verticalScrollBar()
        bar.setValue(bar.maximum())
        # Only let the configured speaker(s) trigger an auto-answer — by default
        # the other side, so your own voice never makes Cluely answer itself.
        speaker_ok = AUTO_FROM == "both" or AUTO_FROM == source.lower()
        if self._live and self.chk_auto.isChecked() and speaker_ok:
            self._maybe_auto(text)

    def _is_echo(self, text, source, now):
        """True if `text` is your own mic ("You") picking up call audio that the
        loopback already captured as "Them" — acoustic echo from open speakers.
        We ONLY ever drop a "You" line: the call ("Them") source drives auto-answers
        and is ground truth for the other party, so it is never discarded even if it
        happens to arrive after a mic echo of the same words."""
        if not ECHO_GUARD or source != "You":
            return False
        norm = _norm_line(text)
        if len(norm) < 8:                       # too short to tell echo from a real backchannel
            return False
        for ts, src, prev in self._recent_lines:
            if src == source or now - ts > ECHO_WINDOW_S:
                continue
            if SequenceMatcher(None, norm, prev).ratio() >= ECHO_RATIO:
                return True
        return False

    def _looks_like_question(self, text):
        """Whether a transcript line is plausibly a question/prompt aimed at me.
        Lenient on purpose — a missed question is worse than an extra suggestion."""
        t = text.lower().strip()
        if any(h in t for h in QUESTION_HINTS):
            return True
        words = t.split()
        return bool(words) and words[0].strip(",.") in QUESTION_WORDS

    def _maybe_auto(self, latest):
        # Must look like a question aimed at me before it's worth anything.
        if not self._looks_like_question(latest):
            return
        # Already answering: stash the newest follow-up and fire it on completion
        # (newest wins — by the time we're free, the latest question is the live one).
        if self._auto_inflight:
            self._pending_auto = latest
            return
        now = time.monotonic()
        total = sum(len(x) for x in self._transcript_buf)
        if now - self._last_auto < AUTO_THROTTLE_S:
            return
        if total - self._last_auto_len < AUTO_MIN_NEWCHARS:
            return
        self._fire_auto()

    def _fire_auto(self):
        """Start an auto-answer now. Records throttle/new-char baselines, marks the
        single-flight gate, and clears any queued follow-up it supersedes."""
        self._last_auto = time.monotonic()
        self._last_auto_len = sum(len(x) for x in self._transcript_buf)
        self._auto_inflight = True
        self._pending_auto = None
        self._start_worker(AUTO_PROMPT, self._recent_transcript(), "auto")

    def _drain_pending_auto(self):
        """Called when an auto-answer finishes. If a follow-up arrived mid-answer,
        fire it immediately — bypassing throttle/new-char gates, since it's an
        explicit queued question, not idle chatter."""
        if self._pending_auto is None:
            return
        if self._live and self.chk_auto.isChecked():
            self._fire_auto()            # clears _pending_auto
        else:
            self._pending_auto = None

    # ---------- Q&A ----------
    def on_ask(self):
        q = self.input.text().strip()
        if not q:
            return
        self.input.clear()
        self._convo.append(("you", q))
        self._render_chat()
        self._start_worker(q, self._recent_transcript(), "claude")

    def _recent_transcript(self):
        return "\n".join(self._transcript_buf)[-4000:]

    def ask_screen(self):
        """Capture the screen and ask Claude about it (on-demand vision)."""
        try:
            from vision import grab_screen_b64
            img = grab_screen_b64(VISION_MONITOR)
        except Exception as exc:
            self._convo.append(("you", "🖥️ screen capture"))
            self._convo.append(("claude", f"_(screen capture failed: {exc})_"))
            self._render_chat()
            return
        self._convo.append(("you", "🖥️ Sent my screen — solve / explain it"))
        self._render_chat()
        self._start_worker(VISION_PROMPT, self._recent_transcript(), "claude", image_b64=img)

    def attach_image(self):
        """Attach an image from the clipboard (preferred) or a file picker."""
        clip = QApplication.clipboard().image()
        if clip is not None and not clip.isNull():
            self._send_image(clip, "clipboard")
            return
        path, _ = QFileDialog.getOpenFileName(
            self, "Choose an image", "",
            "Images (*.png *.jpg *.jpeg *.bmp *.gif *.webp)")
        if not path:
            return
        img = QImage(path)
        if img.isNull():
            self._set_status("could not load that image")
            return
        self._send_image(img, "file")

    def _send_image(self, img, source):
        b64 = _qimage_to_b64(img)
        q = self.input.text().strip()
        self.input.clear()
        tag = "pasted image" if source == "clipboard" else "image file"
        self._convo.append(("you", f"🖼️ {tag} — {q or 'describe / solve it'}"))
        self._render_chat()
        # Empty box on an image = treat it as a full solve (same structured restate +
        # solution + read-aloud as the Solve-screen path).
        question = q or VISION_PROMPT
        self._start_worker(question, self._recent_transcript(), "claude", image_b64=b64)

    def eventFilter(self, obj, event):
        # Ctrl+V in the question box attaches a clipboard image instead of text.
        if obj is self.input and event.type() == QEvent.Type.KeyPress:
            if event.matches(QKeySequence.StandardKey.Paste):
                if not QApplication.clipboard().image().isNull():
                    self.attach_image()
                    return True
        return super().eventFilter(obj, event)

    def _start_worker(self, question, transcript, label, image_b64=""):
        idx = len(self._convo)
        self._convo.append((label, ""))
        self._render_chat()
        self.tabs.setCurrentIndex(0)
        self._set_status("reading screen…" if image_b64 else "thinking…")
        w = AskWorker(question, transcript, self.notes.toPlainText(), image_b64,
                      fast=(label == "auto"))
        w.chunk.connect(lambda s, i=idx: self._append_at(i, s))
        w.finished_text.connect(lambda _t, k=label: self._worker_done(k))
        w.failed.connect(lambda m, i=idx, k=label: self._fail_at(i, m, k))
        w.start()
        self._workers.append(w)
        self._workers = [x for x in self._workers if x.isRunning()] or self._workers

    def _append_at(self, i, s):
        role, text = self._convo[i]
        self._convo[i] = (role, text + s)
        self._render_chat()

    def _worker_done(self, kind="auto"):
        # Only the auto-answer worker owns the single-flight gate; a manual ask
        # finishing must not reopen it (and must not fire a queued follow-up).
        if kind == "auto":
            self._auto_inflight = False
            self._drain_pending_auto()
        self._set_status("hidden from capture")

    def _fail_at(self, i, msg, kind="auto"):
        role, _ = self._convo[i]
        self._convo[i] = (role, f"_(error: {msg})_")
        self._render_chat()
        if kind == "auto":
            self._auto_inflight = False
            self._drain_pending_auto()
        self._set_status("error")

    def _render_chat(self):
        out = []
        for role, text in self._convo:
            if role == "you":
                out.append(f"**You:** {text}")
            elif role == "auto":
                out.append(f"**↳ Suggested:** {text}")
            else:
                out.append(f"**Claude:** {text}")
        self.chat.setMarkdown("\n\n".join(out))
        bar = self.chat.verticalScrollBar()
        bar.setValue(bar.maximum())

    # ---------- hotkey slots ----------
    def toggle_visible(self):
        if self.isVisible():
            self.hide()
        else:
            self.show()
            self.raise_()

    def focus_input(self):
        if not self.isVisible():
            self.show()
        self.raise_()
        self.activateWindow()
        self.tabs.setCurrentIndex(0)
        self.input.setFocus()

    def toggle_clickthrough(self):
        self._click_through = not self._click_through
        hwnd = int(self.winId())
        ex = user32.GetWindowLongW(hwnd, GWL_EXSTYLE)
        if self._click_through:
            ex |= WS_EX_TRANSPARENT | WS_EX_LAYERED
        else:
            ex &= ~WS_EX_TRANSPARENT
        user32.SetWindowLongW(hwnd, GWL_EXSTYLE, ex)
        self._set_status("click-through ON" if self._click_through else "hidden from capture")

    def _quit(self):
        self.close()  # closeEvent does the listener teardown + notes save
        QApplication.quit()

    def _shutdown_listeners(self):
        """Stop and join the active and any retiring listeners. Blocking is fine
        here — the app is exiting — and a bounded wait() guarantees no QThread is
        destroyed while still running (which would abort the process)."""
        pending = list(self._retiring)
        if self._listener:
            pending.append(self._listener)
            self._listener = None
        for t in pending:
            t.stop()
            t.wait(5000)
        self._retiring.clear()

    def closeEvent(self, e):
        self._shutdown_listeners()
        try:
            (NOTES_DIR / "notes-latest.md").write_text(
                self.notes.toPlainText(), encoding="utf-8"
            )
        except Exception:
            pass
        super().closeEvent(e)
