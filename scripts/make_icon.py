"""Generate assets/cluely.ico — a dark rounded square with the panel's cyan dot.
Dev-only build helper; Pillow is not a runtime dependency."""
from pathlib import Path
from PIL import Image, ImageDraw

SIZE = 256
BG = (10, 12, 16, 255)         # #0a0c10 — panel background
ACCENT = (125, 211, 252, 255)  # #7dd3fc — the ● accent

img = Image.new("RGBA", (SIZE, SIZE), (0, 0, 0, 0))
d = ImageDraw.Draw(img)
# rounded-square background
d.rounded_rectangle([8, 8, SIZE - 8, SIZE - 8], radius=44, fill=BG)
# centered accent dot
r = 54
cx = cy = SIZE // 2
d.ellipse([cx - r, cy - r, cx + r, cy + r], fill=ACCENT)

out = Path(__file__).resolve().parent.parent / "assets" / "cluely.ico"
out.parent.mkdir(parents=True, exist_ok=True)
img.save(out, format="ICO", sizes=[(16, 16), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])
print(f"wrote {out}")
