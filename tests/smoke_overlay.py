"""Headless smoke test for the on-screen controls. Run with the venv python:
    .venv\\Scripts\\python.exe tests\\smoke_overlay.py
Exits 0 + prints OK on success; prints FAIL and exits 1 otherwise.
Uses Qt's offscreen platform so it needs no display. Does NOT call toggle_live(True)
(that would start the real audio listener) - it exercises _set_power directly."""
import os, sys
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from PySide6.QtWidgets import QApplication


def main():
    app = QApplication.instance() or QApplication([])
    from overlay import Overlay
    ov = Overlay()

    # close button exists and is labelled
    assert hasattr(ov, "btn_close") and ov.btn_close is not None, "btn_close missing"
    assert ov.btn_close.text() == "×", "close button label"

    # power switch: object name + idle defaults
    assert ov.btn_live.objectName() == "power", "power objectName"
    assert ov._live is False, "should start idle"
    assert ov.btn_live.text() == "⏻ OFF", f"idle label was {ov.btn_live.text()!r}"
    assert ov.btn_live.property("on") is False, "idle property"

    # _set_power flips label + property (pure UI, no listener)
    ov._set_power(True)
    assert ov.btn_live.text() == "⏻ LIVE", "live label"
    assert ov.btn_live.property("on") is True, "live property"
    ov._set_power(False)
    assert ov.btn_live.text() == "⏻ OFF", "back to idle label"
    assert ov.btn_live.property("on") is False, "back to idle property"

    print("OK")


if __name__ == "__main__":
    try:
        main()
    except AssertionError as e:
        print(f"FAIL: {e}"); sys.exit(1)
    except Exception as e:
        print(f"FAIL (error): {type(e).__name__}: {e}"); sys.exit(1)
