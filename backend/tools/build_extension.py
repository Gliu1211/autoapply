"""Generate the extension's form reader (and icons) from the app, so both read forms the same way.
Run: .venv\\Scripts\\python backend\\tools\\build_extension.py"""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))

from app.apply.dom import EXTRACT_JS, SUCCESS_RE  # noqa: E402

EXT = ROOT / "extension"


def build_extract() -> None:
    (EXT / "extract.js").write_text(
        "// Generated from backend/app/apply/dom.py by backend/tools/build_extension.py. Do not edit by hand.\n"
        f"window.__autoapplyExtract = {EXTRACT_JS.strip()};\n"
        f"window.__autoapplySuccess = new RegExp({json.dumps(SUCCESS_RE)}, 'i');\n",
        encoding="utf-8",
    )


ICON_HTML = """<html><body style="margin:0;background:transparent">
<div style="width:{s}px;height:{s}px;border-radius:{r}px;background:#3b5bdb;display:grid;place-items:center">
<svg width="{i}" height="{i}" viewBox="0 0 24 24" fill="none" stroke="#fff" stroke-width="3.2" stroke-linecap="round" stroke-linejoin="round"><path d="m5 13 4 4L19 7"/></svg>
</div></body></html>"""


def build_icons() -> None:
    from playwright.sync_api import sync_playwright
    (EXT / "icons").mkdir(exist_ok=True)
    with sync_playwright() as p:
        b = p.chromium.launch()
        for size in (16, 48, 128):
            pg = b.new_page(viewport={"width": size, "height": size})
            pg.set_content(ICON_HTML.format(s=size, r=max(3, size // 5), i=int(size * 0.62)))
            pg.screenshot(path=str(EXT / "icons" / f"{size}.png"), omit_background=True)
        b.close()


if __name__ == "__main__":
    build_extract()
    if "--no-icons" not in sys.argv:
        build_icons()
    print(f"Extension ready in {EXT}")
