"""Cluely entry point: launches the overlay, warms the Gemini client, and
registers global hotkeys."""
import sys
import threading

from PySide6.QtWidgets import QApplication
from pynput import keyboard

import brain
from config import (
    HOTKEY_ASK, HOTKEY_CLICKTHROUGH, HOTKEY_IMAGE, HOTKEY_LIVE, HOTKEY_QUIT,
    HOTKEY_TOGGLE, HOTKEY_VISION,
)
from overlay import Overlay


def main():
    app = QApplication(sys.argv)
    overlay = Overlay()
    overlay.show()

    # Check the Gemini key and model names in the background, so a missing or
    # bad key shows on the status line at launch, not on the first question.
    def warm():
        problem = brain.warm()
        if problem:
            overlay.sig_status.emit(problem)

    threading.Thread(target=warm, daemon=True).start()

    # pynput runs hotkeys on its own thread; the lambdas only emit Qt signals,
    # which Qt delivers safely onto the UI thread.
    hotkeys = keyboard.GlobalHotKeys({
        HOTKEY_TOGGLE: lambda: overlay.sig_toggle.emit(),
        HOTKEY_ASK: lambda: overlay.sig_focus_input.emit(),
        HOTKEY_CLICKTHROUGH: lambda: overlay.sig_toggle_clickthrough.emit(),
        HOTKEY_LIVE: lambda: overlay.sig_live.emit(),
        HOTKEY_VISION: lambda: overlay.sig_vision.emit(),
        HOTKEY_IMAGE: lambda: overlay.sig_image.emit(),
        HOTKEY_QUIT: lambda: overlay.sig_quit.emit(),
    })
    hotkeys.daemon = True
    hotkeys.start()

    try:
        rc = app.exec()
    finally:
        hotkeys.stop()
    sys.exit(rc)


if __name__ == "__main__":
    main()
