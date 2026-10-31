"""Builds strike-list.user.js from strike-list.src.js, embedding small copies of the logos.
Run from the StrikeList folder:  python3 mobile/make_userscript.py   (needs Pillow)"""
import base64, io
from pathlib import Path
from PIL import Image

here = Path(__file__).resolve().parent
assets = here.parent / "assets"


def b64(im):
    buf = io.BytesIO()
    im.save(buf, "PNG", optimize=True)
    return base64.b64encode(buf.getvalue()).decode()


header = Image.open(assets / "header.png").convert("RGBA").resize((288, 72), Image.LANCZOS)
icon = Image.open(assets / "logo.png").convert("RGBA").resize((72, 72), Image.LANCZOS)
src = (here / "strike-list.src.js").read_text()
out = src.replace("__HEADER__", b64(header)).replace("__ICON__", b64(icon))
(here / "strike-list.user.js").write_text(out)
print(f"wrote strike-list.user.js ({len(out) // 1024} KB)")
