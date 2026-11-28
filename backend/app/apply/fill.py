"""Put an answer into one field, whatever widget it is. Each filler returns True when the value stuck."""
from __future__ import annotations

import logging
import re
from datetime import date
from pathlib import Path

from playwright.async_api import Frame, Locator, Page
from playwright.async_api import Error as PWError
from playwright.async_api import TimeoutError as PWTimeout

from .answers import pick_option

log = logging.getLogger("autoapply.fill")
OPTION_SEL = ('[role="option"]:visible, [data-automation-id="promptOption"]:visible, li[id*="option" i]:visible, '
              '.dropdown-location:visible, .pac-item:visible')


def _loc(frame: Frame, fid: str) -> Locator:
    return frame.locator(f'[data-aa="{fid}"]').first


async def _scroll(loc: Locator) -> None:
    """Bring a field into view. Playwright waits for it to hold still first; on a page whose layout keeps moving
    (an animation, someone scrolling the window) that wait times out, so fall back to the page's own scrolling."""
    try:
        await loc.scroll_into_view_if_needed(timeout=1500)
    except PWTimeout:
        await loc.evaluate("e => e.scrollIntoView({block: 'center', inline: 'nearest'})")


async def _click(loc: Locator, timeout: int = 3000) -> None:
    """Click; if the element never holds still long enough for Playwright, click it where it is now."""
    try:
        await loc.click(timeout=timeout)
    except PWTimeout:
        await loc.click(force=True, timeout=timeout)


async def _blur(loc: Locator) -> None:
    try:
        await loc.evaluate("e => { e.dispatchEvent(new Event('change', {bubbles: true})); e.blur(); }")
    except PWError:
        pass


async def fill_text(frame: Frame, f: dict, value: str) -> bool:
    loc = _loc(frame, f["id"])
    value = str(value)
    if f.get("maxlength") and len(value) > f["maxlength"]:
        value = value[: f["maxlength"]]
    try:
        await _scroll(loc)
        await loc.fill(value, timeout=5000)
    except (PWError, PWTimeout):
        try:
            await _click(loc)
            await loc.press("Control+a")
            await loc.type(value, delay=8)
        except (PWError, PWTimeout):
            return False
    await _blur(loc)
    try:
        return (await loc.input_value(timeout=2000)).strip() != "" or value == ""
    except (PWError, PWTimeout):
        return True


async def fill_select(frame: Frame, f: dict, value) -> bool:
    loc = _loc(frame, f["id"])
    if f.get("multiple") and isinstance(value, list):
        picks = [o for v in value if (o := pick_option(v, f["options"]))]
        if not picks:
            return False
        try:
            await loc.select_option(label=picks, timeout=5000)
            return True
        except (PWError, PWTimeout):
            return False
    pick = pick_option(value, f["options"])
    if not pick:
        return False
    try:
        await loc.select_option(label=pick, timeout=5000)
        await _blur(loc)
        return True
    except (PWError, PWTimeout):
        return False


async def _check(frame: Frame, member: Locator) -> bool:
    try:
        el_id = await member.get_attribute("id")
        if el_id:
            lab = frame.locator(f'label[for="{el_id}"]').first
            if await lab.count() and await lab.is_visible():
                await lab.click(timeout=4000)
                if await member.is_checked():
                    return True
        wrap = member.locator("xpath=ancestor::label[1]")
        if await wrap.count() and await wrap.first.is_visible():
            await wrap.first.click(timeout=4000)
            if await member.is_checked():
                return True
        await member.check(force=True, timeout=4000)
        return await member.is_checked()
    except (PWError, PWTimeout):
        try:
            await member.evaluate("e => { e.click(); }")
            return await member.is_checked()
        except PWError:
            return False


async def fill_choice_group(frame: Frame, f: dict, value) -> bool:
    """radio / checkboxes groups."""
    values = value if isinstance(value, list) else [value]
    if f["kind"] == "radio":
        pick = pick_option(values, f["options"])
        if pick is None:
            return False
        idxs = [f["options"].index(pick)]
    else:
        idxs = sorted({f["options"].index(o) for v in values if (o := pick_option(v, f["options"]))})
        if not idxs:
            return False
    ok = True
    for i in idxs:
        ok &= await _check(frame, frame.locator(f'[data-aa="{f["id"]}.{i}"]').first)
    return ok


async def fill_checkbox(frame: Frame, f: dict, value) -> bool:
    want = str(value).lower() in ("checked", "true", "yes", "1", "y")
    loc = _loc(frame, f["id"])
    try:
        if await loc.is_checked() == want:
            return True
        if want:
            return await _check(frame, loc)
        await loc.uncheck(force=True, timeout=3000)
        return True
    except (PWError, PWTimeout):
        return False


async def fill_buttons(frame: Frame, f: dict, value) -> bool:
    pick = pick_option(value, f["options"])
    if pick is None:
        return False
    try:
        await _click(frame.locator(f'[data-aa="{f["id"]}.{f["options"].index(pick)}"]').first, 4000)
        return True
    except (PWError, PWTimeout):
        return False


async def fill_file(frame: Frame, f: dict, path: Path) -> bool:
    try:
        await _loc(frame, f["id"]).set_input_files(str(path), timeout=8000)
        await frame.wait_for_timeout(1200)
        return True
    except (PWError, PWTimeout) as e:
        log.info("file upload failed: %s", e)
        return False


async def _visible_options(page: Page, frame: Frame) -> tuple[Locator, list[str]]:
    for scope in (frame, page.main_frame) if frame != page.main_frame else (frame,):
        opts = scope.locator(OPTION_SEL)
        try:
            texts = [re.sub(r"\s+", " ", t).strip() for t in await opts.all_inner_texts()]
        except PWError:
            texts = []
        if texts:
            return opts, texts
    return frame.locator(OPTION_SEL), []


async def probe_options(page: Page, frame: Frame, f: dict) -> list[str]:
    """Open a combobox/listbox just to read its options, then close it."""
    loc = _loc(frame, f["id"])
    try:
        await _scroll(loc)
        await _click(loc)
        await page.wait_for_timeout(500)
        _, texts = await _visible_options(page, frame)
        await page.keyboard.press("Escape")
        await page.wait_for_timeout(150)
        return [t for t in texts if t and not re.match(r"^(no options|loading|no results)", t, re.I)][:150]
    except (PWError, PWTimeout):
        return []


_PLACEHOLDER_OPT = re.compile(r"^(no options|no results|loading|searching|type to search|start typing)", re.I)


async def _await_options(page: Page, frame: Frame, timeout_ms: int = 4000) -> tuple[Locator, list[str]]:
    """Poll until an async search list shows real options and stops changing."""
    last: list[str] = []
    opts = frame.locator(OPTION_SEL)
    waited = 0
    while waited < timeout_ms:
        await page.wait_for_timeout(350)
        waited += 350
        opts, texts = await _visible_options(page, frame)
        texts = [t for t in texts if t and not _PLACEHOLDER_OPT.match(t)]
        if texts and texts == last:
            return opts, texts
        last = texts
    return opts, last


async def fill_combobox(page: Page, frame: Frame, f: dict, value, strict: bool) -> bool:
    """React-select / autocomplete inputs: type, wait for the list to settle, click the best matching option."""
    cands = [c for c in (value if isinstance(value, list) else [value]) if c]
    if not cands:
        return False
    loc = _loc(frame, f["id"])
    best = pick_option(cands, f.get("options") or [])
    queries = ([best[:60]] if best else []) + [str(c)[:60] for c in cands[:3]]
    words = re.split(r"[\s,]+", queries[0])
    if len(words) > 3:
        queries.append(" ".join(words[:3]))   # async search boxes often match better on a shorter prefix
    tried = set()
    for qtext in queries:
        if qtext in tried:
            continue
        tried.add(qtext)
        try:
            await _scroll(loc)
            await _click(loc)
            try:
                await loc.fill("", timeout=1500)
            except (PWError, PWTimeout):
                pass
            await loc.type(qtext, delay=20)
            opts, texts = await _await_options(page, frame)
            # only exact-ish matches; never "first option" for a strict list
            pick = pick_option(cands, texts) if texts else None
            if pick is None and texts and not strict:
                pick = texts[0]
            if pick is not None:
                await _click(opts.nth(texts.index(pick)), 4000)
                await page.wait_for_timeout(300)
                return True
            await page.keyboard.press("Escape")
        except (PWError, PWTimeout) as e:
            log.info("combobox %s failed: %s", f["question"][:40], e)
    return False


async def fill_listbox(page: Page, frame: Frame, f: dict, value) -> bool:
    """Workday-style dropdown buttons."""
    loc = _loc(frame, f["id"])
    try:
        await _scroll(loc)
        await _click(loc, 4000)
        await page.wait_for_timeout(600)
        opts, texts = await _visible_options(page, frame)
        pick = pick_option(value, texts)
        if pick is None:
            await page.keyboard.press("Escape")
            return False
        target = opts.nth(texts.index(pick))
        await _scroll(target)
        await _click(target, 4000)
        await page.wait_for_timeout(400)
        return True
    except (PWError, PWTimeout):
        return False


async def fill_multiselect(page: Page, frame: Frame, f: dict, value) -> bool:
    """Workday prompt fields (How did you hear, Field of study, Skills...). Type, Enter, pick; handles one sub-menu level."""
    cands = value if isinstance(value, list) else [value]
    loc = _loc(frame, f["id"])
    for cand in cands[:5]:
        if not cand:
            continue
        try:
            await _scroll(loc)
            await _click(loc)
            await loc.fill(str(cand)[:50], timeout=3000)
            await loc.press("Enter")
            await page.wait_for_timeout(1300)
            opts, texts = await _visible_options(page, frame)
            pick = pick_option(cands, texts) or (texts[0] if texts else None)
            if pick is None:
                continue
            await _click(opts.nth(texts.index(pick)), 4000)
            await page.wait_for_timeout(800)
            # nested category (e.g. "Job Board" > "LinkedIn")
            opts2, texts2 = await _visible_options(page, frame)
            if texts2 and texts2 != texts:
                pick2 = pick_option(cands, texts2) or texts2[0]
                await _click(opts2.nth(texts2.index(pick2)), 4000)
                await page.wait_for_timeout(500)
            await page.keyboard.press("Escape")
            return True
        except (PWError, PWTimeout):
            continue
    return False


def _date_parts(value: str) -> dict:
    v = str(value).strip()
    out = {}
    m = re.match(r"(\d{1,2})/(\d{1,2})/(\d{4})", v)
    if m:
        return {"month": m.group(1).zfill(2), "day": m.group(2).zfill(2), "year": m.group(3)}
    m = re.match(r"(\d{4})-(\d{2})(?:-(\d{2}))?", v)
    if m:
        return {"year": m.group(1), "month": m.group(2), "day": m.group(3) or "01"}
    m = re.match(r"(\d{1,2})/(\d{4})", v)
    if m:
        return {"month": m.group(1).zfill(2), "year": m.group(2), "day": "01"}
    months = "jan feb mar apr may jun jul aug sep oct nov dec".split()
    m = re.match(r"([A-Za-z]{3})[a-z]*\.?\s+(\d{4})", v)
    if m and m.group(1).lower() in months:
        return {"month": str(months.index(m.group(1).lower()) + 1).zfill(2), "year": m.group(2), "day": "01"}
    m = re.search(r"(\d{4})", v)
    if m:
        out = {"year": m.group(1), "month": "05", "day": "01"}
    return out


async def fill_date(frame: Frame, f: dict, value: str) -> bool:
    parts = _date_parts(value or date.today().strftime("%m/%d/%Y"))
    if not parts:
        return False
    kinds = f.get("options") or []
    ok = True
    for i, kind in enumerate(kinds):
        v = parts.get(kind or ["month", "day", "year"][min(i, 2)])
        if not v:
            continue
        loc = frame.locator(f'[data-aa="{f["id"]}.{i}"]').first
        try:
            await _click(loc)
            await loc.type(v, delay=40)
        except (PWError, PWTimeout):
            ok = False
    return ok
