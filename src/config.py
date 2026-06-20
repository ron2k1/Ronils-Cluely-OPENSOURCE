"""Central configuration for Cluely.

Everything is overridable via environment variables so you can tune behaviour
without editing code (e.g. set CLUELY_WHISPER_MODEL=medium.en for accuracy).
"""
import os
import shutil
from pathlib import Path

APP_NAME = "Cluely"
BASE_DIR = Path(__file__).resolve().parent.parent
NOTES_DIR = BASE_DIR / "notes"
NOTES_DIR.mkdir(exist_ok=True)

# --- Headless Claude (uses your existing `claude` login, no API key needed) ---
def _resolve_claude_cmd() -> str:
    """Find the Claude CLI without assuming how it was installed.

    Order: explicit override, then whatever `claude` is on PATH (covers the
    native installer, winget, and an npm global install), then the npm-global
    default as a last resort. So a fresh clone works for anyone who has the
    CLI installed and logged in, not only npm users.
    """
    override = os.environ.get("CLUELY_CLAUDE_CMD")
    if override:
        return override
    found = shutil.which("claude") or shutil.which("claude.cmd")
    if found:
        return found
    return str(Path(os.environ.get("APPDATA", "")) / "npm" / "claude.cmd")


CLAUDE_CMD = _resolve_claude_cmd()
CLAUDE_MODEL = os.environ.get("CLUELY_CLAUDE_MODEL", "")  # "" = account default
# Voiced auto-answers run on a FAST model so they're near-instant; vision/typed
# questions keep the stronger CLAUDE_MODEL. Set "" to use one shared session.
AUTO_MODEL = os.environ.get("CLUELY_AUTO_MODEL", "haiku")
# Hang recovery: if a turn produces no result within this many seconds, kill +
# respawn the claude process so a stalled turn can't wedge the session lock.
ANSWER_TIMEOUT_S = float(os.environ.get("CLUELY_ANSWER_TIMEOUT", "90"))

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
HOTKEY_VISION = "<ctrl>+<alt>+s"        # capture the screen and ask Claude about it
HOTKEY_IMAGE = "<ctrl>+<alt>+i"         # attach an image (clipboard, else file picker)
HOTKEY_QUIT = "<ctrl>+<alt>+q"          # save notes and exit
