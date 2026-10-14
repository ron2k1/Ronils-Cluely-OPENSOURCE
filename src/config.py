"""Central configuration for Cluely.

Everything is overridable via environment variables so you can tune behaviour
without editing code (e.g. set CLUELY_WHISPER_MODEL=medium.en for accuracy).
"""
import os
from pathlib import Path

APP_NAME = "Cluely"
BASE_DIR = Path(__file__).resolve().parent.parent
NOTES_DIR = BASE_DIR / "notes"
NOTES_DIR.mkdir(exist_ok=True)

# --- Gemini API (get a key at https://aistudio.google.com/apikey) ---
# The key is read from this environment variable and never stored in the repo.
API_KEY_ENV = "GEMINI_API_KEY"
# Typed + vision (code) questions: the strongest Flash model. low / medium / high.
# medium answered hard problems in a median 21s against high's 54s, and solved 5
# of 6 to high's 6 of 6 (measured), so it's the default; high is one setting away.
MODEL = os.environ.get("CLUELY_MODEL", "gemini-3.8-flash")
THINKING = os.environ.get("CLUELY_THINKING", "medium")
# Voiced auto-answers: the fastest model, so a spoken answer starts in about a
# second. "" thinking = the model's own default. Set CLUELY_AUTO_MODEL="" to
# answer voice on MODEL too.
AUTO_MODEL = os.environ.get("CLUELY_AUTO_MODEL", "gemini-3.5-flash-lite") or MODEL
AUTO_THINKING = os.environ.get("CLUELY_AUTO_THINKING", "")
# Follow-ups ("explain line 4") see this many previous question/answer pairs.
HISTORY_TURNS = int(os.environ.get("CLUELY_HISTORY_TURNS", "3"))
# Seconds before a typed/vision turn is abandoned. It stays long so high still
# works if you set it: at high, a hard problem sent nothing at all for 119-147s
# before its first word (measured), and there is no way to tell that silence from
# a dead connection.
# Voiced answers get a short limit so a stall can't hold up the auto-answer queue.
ANSWER_TIMEOUT_S = float(os.environ.get("CLUELY_ANSWER_TIMEOUT", "300"))
AUTO_ANSWER_TIMEOUT_S = float(os.environ.get("CLUELY_AUTO_TIMEOUT", "30"))

# --- Local transcription (faster-whisper) ---
# CPU/base.en is the reliable default; set CLUELY_WHISPER_DEVICE=cuda for GPU.
WHISPER_MODEL = os.environ.get("CLUELY_WHISPER_MODEL", "base.en")
WHISPER_DEVICE = os.environ.get("CLUELY_WHISPER_DEVICE", "cpu")
WHISPER_COMPUTE = os.environ.get("CLUELY_WHISPER_COMPUTE", "int8")

# --- Live listening sources ---
# Capture the other participants (system audio via loopback) and/or your own mic.
# Both default on, so a solo test transcribes and a real meeting hears both sides.
CAPTURE_SYSTEM = os.environ.get("CLUELY_CAPTURE_SYSTEM", "1") == "1"
CAPTURE_MIC = os.environ.get("CLUELY_CAPTURE_MIC", "1") == "1"
SPEAKER_DEVICE = os.environ.get("CLUELY_SPEAKER_DEVICE", "")  # "" = system default
MIC_DEVICE = os.environ.get("CLUELY_MIC_DEVICE", "")          # "" = system default

# --- Always-on auto-answer ---
AUTO_THROTTLE_S = float(os.environ.get("CLUELY_AUTO_THROTTLE", "3"))
AUTO_MIN_NEWCHARS = int(os.environ.get("CLUELY_AUTO_MINCHARS", "12"))
# Which speaker's lines may trigger an auto-answer: them / you / both.
AUTO_FROM = os.environ.get("CLUELY_AUTO_FROM", "them").strip().lower()
# Your name (first name is enough) so auto-answer knows a question is aimed at
# you when someone says it out loud ("Ada, what do you think?"). Empty by
# default, so no personal name is baked into the code. Set it to turn the name
# trigger on. Multiple words are each matched (e.g. "Ada Lovelace" -> ada, lovelace).
MY_NAME = os.environ.get("CLUELY_MY_NAME", "").strip()

# --- Echo / cross-talk guard ---
# Drop a line that near-duplicates a recent line from the OTHER source within
# ECHO_WINDOW_S seconds (similarity >= ECHO_RATIO). This catches acoustic echo
# (open speakers re-heard by the mic) and mic sidetone leaking into the loopback
# stream, so the far side isn't double-logged and your own voice can't leak in.
ECHO_GUARD = os.environ.get("CLUELY_ECHO_GUARD", "1") == "1"
ECHO_WINDOW_S = float(os.environ.get("CLUELY_ECHO_WINDOW", "6"))
ECHO_RATIO = float(os.environ.get("CLUELY_ECHO_RATIO", "0.80"))

# --- On-demand screen vision ---
# Which monitor to capture: 0 = all screens, 1 = primary, 2 = next, …
VISION_MONITOR = int(os.environ.get("CLUELY_VISION_MONITOR", "1"))

# --- UI ---
OPACITY = float(os.environ.get("CLUELY_OPACITY", "0.93"))

# --- Global hotkeys (pynput format) ---
HOTKEY_TOGGLE = "<ctrl>+<alt>+h"        # show / hide the overlay
HOTKEY_ASK = "<ctrl>+<alt>+a"           # show + focus the question box
HOTKEY_CLICKTHROUGH = "<ctrl>+<alt>+t"  # let the mouse pass through the panel
HOTKEY_LIVE = "<ctrl>+<alt>+<space>"    # toggle live mode (listen + auto-answer)
HOTKEY_VISION = "<ctrl>+<alt>+s"        # capture the screen and ask Gemini about it
HOTKEY_IMAGE = "<ctrl>+<alt>+i"         # attach an image (clipboard, else file picker)
HOTKEY_QUIT = "<ctrl>+<alt>+q"          # save notes and exit
