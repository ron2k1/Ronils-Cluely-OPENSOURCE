"""Gemini backend: streams answers from the Gemini API.

Two sessions share one API client. The main session answers typed and vision
(code) questions on a strong model with deep thinking; the auto session answers
live voiced questions on the fastest model with a lean preamble.

Calls are stateless (`generate_content_stream`), so Google keeps no conversation
state server-side; prompts carry other participants' words from the call. Each
session instead remembers its last few question/answer pairs in memory, text
only, so a follow-up like "explain line 4" works without re-sending the
transcript or the image.
"""
import base64
import os
import threading
import time

from google import genai
from google.genai import errors, types

from config import (
    ANSWER_TIMEOUT_S, API_KEY_ENV, AUTO_ANSWER_TIMEOUT_S, AUTO_MODEL, AUTO_THINKING,
    HISTORY_TURNS, MODEL, THINKING,
)

SYSTEM_PREAMBLE = (
    "You are Cluely, a private real-time copilot for the user during their own "
    "meetings and presentations. You may receive a live transcript of the meeting "
    "and the user's running notes. Answer concisely and in immediately useful form "
    "— short enough to glance at and say out loud. For 'summarize' or 'notes' "
    "requests, return tight bullet points. "
    "Whenever your answer contains code, FIRST restate the problem in one or two plain "
    "first-person sentences under a 'Problem (say this first):' heading so I can say it "
    "out loud and show I understood what was asked; then present the solution as plain "
    "code in a fenced code block with NO line numbers, so it pastes and runs as is; then "
    "finish with a section headed 'Read aloud (line by line):' — a spoken script that "
    "walks through the code in order, covering every line, each entry starting with that "
    "code line in backticks ('`<code line>` — ...'). Write it in a natural, "
    "conversational first-person voice — the way a person actually talks through "
    "their own code: contractions, plain everyday words, relaxed but clear. Not "
    "stiff, not robotic, no textbook phrasing, short plain sentences. Default to two "
    "sentences per line: what it does and why it's needed. Add a third only when it says "
    "something new, like the edge case it handles or how it fits the approach, never a "
    "summary like 'it keeps things clean'. Base cases, loops, recursive calls, and undo "
    "steps all count as logic. Only truly trivial lines (the class header, a bare return, "
    "continue, or else, a plain empty list) get a single sentence. "
    "Put each entry on its own line. Write math and complexity as plain text "
    "(O(n log n), r + c, n x n), never LaTeX or dollar signs. "
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


def build_prompt(question: str, transcript: str = "", notes: str = "") -> str:
    """The user turn: recent transcript, running notes, then the question. The
    preamble rides separately as the system instruction."""
    parts = []
    if transcript and transcript.strip():
        parts += ["## Live transcript (recent)", transcript.strip()[-6000:], ""]
    if notes and notes.strip():
        parts += ["## My notes", notes.strip()[-3000:], ""]
    parts += ["## My question", question.strip()]
    return "\n".join(parts)


def _chunk_text(chunk) -> str:
    """Answer text from one streamed response, skipping any thought parts."""
    out = []
    for cand in chunk.candidates or []:
        if cand.content and cand.content.parts:
            out += [p.text for p in cand.content.parts if p.text and not p.thought]
    return "".join(out)


def _stop_reason(chunk) -> str:
    """Why the model stopped early (a blocked prompt, or a finish other than a
    normal STOP such as RECITATION or MAX_TOKENS), or "" for a normal finish."""
    feedback = chunk.prompt_feedback
    if feedback and feedback.block_reason:
        return f"blocked: {getattr(feedback.block_reason, 'name', feedback.block_reason)}"
    for cand in chunk.candidates or []:
        reason = cand.finish_reason
        if reason and reason not in (types.FinishReason.STOP,
                                     types.FinishReason.FINISH_REASON_UNSPECIFIED):
            return f"stopped: {getattr(reason, 'name', reason)}"
    return ""


class MissingKeyError(RuntimeError):
    """GEMINI_API_KEY is not set."""


_client = None
_client_lock = threading.Lock()


def _shared_client():
    """One API client for both sessions, built on first use so a missing key
    surfaces as a readable error in the panel instead of a crash at import."""
    global _client
    with _client_lock:
        if _client is None:
            key = os.environ.get(API_KEY_ENV, "").strip()
            if not key:
                raise MissingKeyError(
                    f"{API_KEY_ENV} is not set. Create a key at "
                    "https://aistudio.google.com/apikey and set it as a user "
                    "environment variable, then restart Cluely.")
            _client = genai.Client(api_key=key)
        return _client


class _Session:
    """One model + preamble + short in-memory history, serving one turn at a time."""

    def __init__(self, model, thinking="", lean=False, timeout_s=ANSWER_TIMEOUT_S,
                 history_turns=HISTORY_TURNS, client=None):
        self._model = model
        self._thinking = thinking
        self._lean = lean
        self._timeout_s = timeout_s
        self._history_turns = history_turns
        self._client = client
        self._history: list[types.Content] = []
        self._lock = threading.Lock()

    def _get_client(self):
        return self._client or _shared_client()

    def _config(self) -> types.GenerateContentConfig:
        thinking = None
        if self._thinking:
            # The SDK's enum only warns on an unknown value and sends it anyway,
            # so check membership ourselves: a typo like "hgih" fails here.
            name = self._thinking.strip().upper()
            if name not in types.ThinkingLevel.__members__:
                raise ValueError(f"unknown thinking level {self._thinking!r}; "
                                 "use low, medium or high")
            thinking = types.ThinkingConfig(thinking_level=types.ThinkingLevel[name])
        # The per-request timeout is both how long httpx waits between bytes and
        # the X-Server-Timeout the SDK sends, so the server gives up at the same point.
        return types.GenerateContentConfig(
            system_instruction=LEAN_PREAMBLE if self._lean else SYSTEM_PREAMBLE,
            thinking_config=thinking,
            http_options=types.HttpOptions(timeout=int(self._timeout_s * 1000)))

    def _stream(self, contents, on_text) -> tuple[str, str]:
        """Stream one answer; returns (text, stop reason or ""). The HTTP timeout
        catches silence; the deadline catches a stream trickling past the budget."""
        deadline = time.monotonic() + self._timeout_s
        collected: list[str] = []
        reason = ""
        stream = self._get_client().models.generate_content_stream(
            model=self._model, contents=contents, config=self._config())
        for chunk in stream:
            text = _chunk_text(chunk)
            if text:
                collected.append(text)
                if on_text:
                    on_text(text)
            reason = _stop_reason(chunk) or reason
            if time.monotonic() > deadline:
                raise TimeoutError(f"answer took longer than {self._timeout_s:.0f}s")
        return "".join(collected), reason

    def warm(self):
        """Validate the config and look the model up: no tokens spent, but it
        proves the thinking level, the key, and the model name. Raises on failure."""
        self._config()
        self._get_client().models.get(model=self._model)

    def ask(self, question, transcript="", notes="", on_text=None, image_b64="") -> str:
        parts = []
        if image_b64:
            parts.append(types.Part.from_bytes(
                data=base64.b64decode(image_b64), mime_type="image/png"))
        parts.append(types.Part.from_text(text=build_prompt(question, transcript, notes)))
        with self._lock:
            out, reason = self._stream(
                self._history + [types.Content(role="user", parts=parts)], on_text)
            if not out.strip():
                raise RuntimeError(f"no answer from Gemini ({reason or 'empty response'})")
            if reason:
                # A cut-off code answer must say so; silently incomplete code is wrong code.
                note = f"\n\n_({reason}; the answer may be incomplete)_"
                if on_text:
                    on_text(note)
                out += note
            asked = question.strip() + (" [about an attached image]" if image_b64 else "")
            self._history += [
                types.Content(role="user", parts=[types.Part.from_text(text=asked)]),
                types.Content(role="model", parts=[types.Part.from_text(text=out)]),
            ]
            self._history = self._history[-2 * self._history_turns:] if self._history_turns else []
        return out

    def reset(self):
        with self._lock:
            self._history = []


# Strong session for typed + vision (code) questions; a fast one for live voiced
# auto-answers. Separate sessions keep separate preambles and histories.
_main = _Session(MODEL, THINKING)
_auto = _Session(AUTO_MODEL, AUTO_THINKING, lean=True, timeout_s=AUTO_ANSWER_TIMEOUT_S)


def warm() -> str:
    """Check the thinking levels, the key, and both model names at launch.
    Returns a short problem for the status line, or "" when all is good."""
    try:
        _main.warm()
        _auto.warm()
    except errors.APIError as exc:
        return f"⚠ Gemini {exc.code}: {exc.status}"
    except MissingKeyError:
        return f"⚠ {API_KEY_ENV} not set"
    except ValueError as exc:
        return f"⚠ {exc}"
    except Exception as exc:
        return f"⚠ Gemini unreachable ({type(exc).__name__})"
    return ""


def ask(question, transcript="", notes="", on_text=None, image_b64="", fast=False) -> str:
    session = _auto if fast else _main
    return session.ask(question, transcript, notes, on_text, image_b64)


def reset():
    _main.reset()
    _auto.reset()
