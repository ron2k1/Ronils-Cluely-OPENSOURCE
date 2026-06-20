"""On-demand screen capture for vision queries.

Grabs a monitor as a PNG (base64) so it can be sent to Claude as an image. We
use mss, which captures via GDI BitBlt — the same path that honors
WDA_EXCLUDEFROMCAPTURE — so Cluely's own (capture-excluded) overlay does NOT
appear in the shot. What you get is whatever is *behind* the panel: the Zoom
window, a shared screen, a coding problem, etc.
"""
import base64

import mss
import mss.tools


def grab_screen_b64(monitor: int = 1) -> str:
    """Return a base64 PNG of one monitor. monitor: 0=all screens, 1=primary."""
    with mss.mss() as sct:
        mons = sct.monitors  # [0] = union of all, [1] = primary, [2] = next…
        idx = monitor if 0 <= monitor < len(mons) else 1
        shot = sct.grab(mons[idx])
        png = mss.tools.to_png(shot.rgb, shot.size)
    return base64.b64encode(png).decode("ascii")
