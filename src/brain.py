"""Headless Claude backend — persistent warm session for low latency.

A cold `claude -p` pays full session-init (SessionStart hooks: vault digest,
memory consolidation, skill reindex) on every call — measured ~7s. That is far
too slow for live answering. Instead we keep ONE `claude` process alive in
stream-json *input* mode: it reads newline-delimited JSON user messages from
stdin until stdin closes. We never close stdin, so the hooks run exactly once at
startup and every subsequent question is just a model round-trip (~2-3s).

Uses your existing Claude login, so there is no API key and no per-token cost.
"""
import json
import subprocess
import threading

from config import ANSWER_TIMEOUT_S, AUTO_MODEL, CLAUDE_CMD, CLAUDE_MODEL

CREATE_NO_WINDOW = 0x08000000

SYSTEM_PREAMBLE = (
    "You are Cluely, a private real-time copilot for the user during their own "
    "meetings and presentations. You may receive a live transcript of the meeting "
    "and the user's running notes. Answer concisely and in immediately useful form "
    "— short enough to glance at and say out loud. For 'summarize' or 'notes' "
    "requests, return tight bullet points. "
    "Whenever your answer contains code, FIRST restate the problem in one or two plain "
    "first-person sentences under a 'Problem (say this first):' heading so I can say it "
    "out loud and show I understood what was asked; then present the solution with a line "
    "number at the start of every line, then finish with a section headed "
    "'Read aloud (line by line):' — a spoken script keyed to those line numbers "
    "('Line 1: ...', 'Line 2: ...', covering every line). Write it in a natural, "
    "conversational first-person voice — the way a person actually talks through "
    "their own code: contractions, plain everyday words, relaxed but clear. Not "
    "stiff, not robotic, no textbook phrasing. One short spoken sentence per line "
    "saying what it does and why. "
    "You assist the user's own thinking; "
    "never produce content intended to deceive an interviewer, examiner, or anyone "
    "evaluating the user's own ability."
)


# Lean preamble for live voiced answers: no code-protocol priming, just a fast,
# say-it-out-loud reply. Keeping it short cuts input tokens and time-to-first-token.
LEAN_PREAMBLE = (
    "You are Cluely, the user's private real-time copilot in their own meeting. "
    "From the live transcript, give a crisp answer the user can say out loud right "
    "now: 1-2 plain sentences, no preamble, no lists. If nothing was actually asked "
    "of the user, give one short bullet of the key point just discussed. "
    "Never produce content meant to deceive anyone evaluating the user's own ability."
)


def build_prompt(question: str, transcript: str = "", notes: str = "", lean: bool = False) -> str:
    parts = [LEAN_PREAMBLE if lean else SYSTEM_PREAMBLE, ""]
    if transcript and transcript.strip():
        parts += ["## Live transcript (recent)", transcript.strip()[-6000:], ""]
    if notes and notes.strip():
        parts += ["## My notes", notes.strip()[-3000:], ""]
    parts += ["## My question", question.strip()]
    return "\n".join(parts)


def _extract(evt: dict, state: dict) -> str:
    """Pull display text out of one stream-json event.

    Handles both partial deltas (--include-partial-messages) and the full
    assistant message. Once we have seen any delta we ignore the final assistant
    message so the answer is not printed twice.
    """
    etype = evt.get("type")
    if etype == "stream_event":
        ev = evt.get("event", {})
        if ev.get("type") == "content_block_delta":
            delta = ev.get("delta", {})
            if delta.get("type") == "text_delta":
                state["saw_delta"] = True
                return delta.get("text", "")
        return ""
    if etype == "assistant" and not state.get("saw_delta"):
        out = []
        for block in evt.get("message", {}).get("content", []):
            if block.get("type") == "text":
                out.append(block.get("text", ""))
        return "".join(out)
    return ""


def _user_message(text: str, image_b64: str = "") -> str:
    content = []
    if image_b64:
        content.append({"type": "image", "source": {
            "type": "base64", "media_type": "image/png", "data": image_b64}})
    content.append({"type": "text", "text": text})
    return json.dumps({"type": "user", "message": {"role": "user", "content": content}})


class _Session:
    """A single long-lived `claude` process serving many turns over one pipe."""

    def __init__(self, model=""):
        self._proc = None
        self._model = model
        self._lock = threading.Lock()

    def _alive(self) -> bool:
        return self._proc is not None and self._proc.poll() is None

    def _spawn(self):
        cmd = [
            "cmd", "/c", CLAUDE_CMD, "-p",
            "--input-format", "stream-json",
            "--output-format", "stream-json",
            "--verbose", "--include-partial-messages",
        ]
        if self._model:
            cmd += ["--model", self._model]
        self._proc = subprocess.Popen(
            cmd,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            encoding="utf-8",
            errors="replace",
            bufsize=1,
            creationflags=CREATE_NO_WINDOW,
        )

    def _kill_proc(self):
        """Force-kill the current process (watchdog callback). Killing it closes
        stdout, which unblocks the reader loop in _send_and_read with EOF."""
        p = self._proc
        if p is not None:
            try:
                p.kill()
            except Exception:
                pass

    def _send_and_read(self, prompt: str, on_text, image_b64: str = "") -> str:
        """Write one user message and read events until the turn's result.

        A watchdog timer guards against a wedged turn: if no `result` event
        arrives within ANSWER_TIMEOUT_S the process is killed, the blocking
        stdout iteration returns at EOF, and we fall through with whatever
        (likely empty) text we have. The caller then raises/respawns — so a
        single hung turn can never hold the session lock indefinitely.
        """
        self._proc.stdin.write(_user_message(prompt, image_b64) + "\n")
        self._proc.stdin.flush()

        watchdog = threading.Timer(ANSWER_TIMEOUT_S, self._kill_proc)
        watchdog.daemon = True
        watchdog.start()

        state: dict = {}
        collected: list[str] = []
        try:
            for line in self._proc.stdout:
                line = line.strip()
                if not line:
                    continue
                try:
                    evt = json.loads(line)
                except json.JSONDecodeError:
                    continue  # hook noise / non-JSON lines
                chunk = _extract(evt, state)
                if chunk:
                    collected.append(chunk)
                    if on_text:
                        on_text(chunk)
                if evt.get("type") == "result":
                    break  # turn complete
        finally:
            watchdog.cancel()
        return "".join(collected)

    def warm(self):
        """Pre-pay session-init (hooks) and one model turn so the first real
        question is instant. Safe to call from a background thread at startup."""
        with self._lock:
            if not self._alive():
                self._spawn()
                try:
                    self._send_and_read("Reply with the single word READY.", None)
                except Exception:
                    pass

    def ask(self, question, transcript="", notes="", on_text=None,
            stop_event=None, image_b64="", lean=False) -> str:
        prompt = build_prompt(question, transcript, notes, lean=lean)
        with self._lock:
            if not self._alive():
                self._spawn()
            try:
                out = self._send_and_read(prompt, on_text, image_b64)
            except (BrokenPipeError, OSError, ValueError):
                self._spawn()  # pipe died; restart once and retry
                out = self._send_and_read(prompt, on_text, image_b64)
        if not out.strip():
            raise RuntimeError(
                "no response (session may have died or needs `claude` re-login)"
            )
        return out

    def shutdown(self):
        with self._lock:
            if self._proc is not None:
                try:
                    self._proc.stdin.close()
                except Exception:
                    pass
                self._proc.terminate()
                self._proc = None


# Strong session for typed + vision (code) questions; a separate FAST session for
# live voiced auto-answers. If AUTO_MODEL is unset/equal, both names point at one
# session so we don't pay for two processes.
_main = _Session(CLAUDE_MODEL)
_auto = _main if (not AUTO_MODEL or AUTO_MODEL == CLAUDE_MODEL) else _Session(AUTO_MODEL)


def _reset():
    """Drop accumulated conversation history by respawning the warm session(s).
    The next ask() lazily respawns, so this is just a clean teardown."""
    _auto.shutdown()
    if _auto is not _main:
        _main.shutdown()


def warm():
    _main.warm()
    if _auto is not _main:
        _auto.warm()


def ask(question, transcript="", notes="", on_text=None, stop_event=None,
        image_b64="", fast=False) -> str:
    session = _auto if fast else _main
    return session.ask(question, transcript, notes, on_text, stop_event,
                       image_b64, lean=fast)


def reset():
    _reset()


def shutdown():
    _reset()
