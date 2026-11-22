"""One persistent browser profile shared by every application, so sign-ins (Handshake, Workday) stick."""
from __future__ import annotations

import asyncio
import logging
from pathlib import Path

from playwright.async_api import BrowserContext, Page, Playwright, async_playwright
from playwright.async_api import Error as PWError

from .. import settings as cfg
from ..config import BROWSER_CDP_URL, BROWSER_CHANNEL, BROWSER_PROFILE

log = logging.getLogger("autoapply.browser")


class BrowserUnavailable(RuntimeError):
    """No browser could be started. Callers should requeue the work and pause, not burn through the queue."""


class Browser:
    def __init__(self) -> None:
        self.pw: Playwright | None = None
        self.ctx: BrowserContext | None = None
        self.headless: bool | None = None
        self.lock = asyncio.Lock()
        self.force_headed = False
        self.channel: str | None = None

    @staticmethod
    def wanted_channel() -> str:
        ch = (cfg.get().get("browser") or BROWSER_CHANNEL or "chrome").lower()
        return ch if ch in ("chrome", "msedge", "chromium") else "chrome"

    @staticmethod
    def profile_dir(channel: str) -> Path:
        # Chrome keeps the original folder; other browsers get their own (profiles aren't interchangeable).
        return BROWSER_PROFILE if channel == "chrome" else BROWSER_PROFILE.parent / f"browser-profile-{channel}"

    @property
    def name(self) -> str:
        return {"chrome": "Chrome", "msedge": "Edge", "chromium": "Chromium"}.get(self.channel or "", "browser")

    async def _playwright(self) -> Playwright:
        if self.pw is None:
            self.pw = await async_playwright().start()
        return self.pw

    async def context(self) -> BrowserContext:
        async with self.lock:
            want_headless = bool(cfg.get()["headless"]) and not self.force_headed
            if self.ctx is not None and not BROWSER_CDP_URL and (
                    self.headless != want_headless or self.channel != self.wanted_channel()):
                await self._close()
            if self.ctx is None:
                await self._start(want_headless)
            return self.ctx

    async def _reset_driver(self) -> None:
        """The Playwright driver can die (sleep, network loss); every launch then fails with 'has been closed'."""
        self.ctx = None
        if self.pw is not None:
            try:
                await self.pw.stop()
            except Exception:  # noqa: BLE001 — it is already dead
                pass
        self.pw = None

    async def _start(self, headless: bool) -> None:
        try:
            await self._launch(headless)
        except (PWError, RuntimeError) as e:
            log.warning("browser launch failed (%s); restarting the Playwright driver and trying again", e)
            await self._reset_driver()
            await asyncio.sleep(2)
            await self._launch(headless)

    async def _launch(self, headless: bool) -> None:
        pw = await self._playwright()
        if BROWSER_CDP_URL:
            b = await pw.chromium.connect_over_cdp(BROWSER_CDP_URL)
            self.ctx = b.contexts[0] if b.contexts else await b.new_context()
        else:
            kwargs = dict(headless=headless, accept_downloads=False,
                          args=["--no-first-run", "--no-default-browser-check"])
            if headless:
                kwargs["viewport"] = {"width": 1280, "height": 900}
            else:
                kwargs["no_viewport"] = True
            first = self.wanted_channel()
            order = [first] + [c for c in ("chrome", "msedge", "chromium") if c != first]
            errors = []
            for ch in order:
                prof = self.profile_dir(ch)
                prof.mkdir(parents=True, exist_ok=True)
                try:
                    self.ctx = await pw.chromium.launch_persistent_context(
                        str(prof), channel=None if ch == "chromium" else ch, **kwargs)
                    self.channel = ch
                    break
                except PWError as e:
                    errors.append(f"{ch}: {str(e).splitlines()[0][:160]}")
                    log.warning("couldn't launch %s: %s", ch, e)
            if self.ctx is None:
                raise RuntimeError("Couldn't start a browser. " + " | ".join(errors))
        self.ctx.set_default_timeout(15000)
        self.ctx.on("close", lambda *_: setattr(self, "ctx", None))
        self.headless = headless

    async def _close(self) -> None:
        if self.ctx is not None:
            try:
                await self.ctx.close()
            except PWError:
                pass
        self.ctx = None

    async def stop(self) -> None:
        await self._close()
        if self.pw is not None:
            await self.pw.stop()
            self.pw = None

    async def new_page(self) -> Page:
        """A new tab. Raises BrowserUnavailable if no browser can be started even after a driver restart."""
        try:
            ctx = await self.context()
            try:
                return await ctx.new_page()
            except PWError:
                # Window closed or browser crashed: relaunch once, restarting the driver if needed.
                self.ctx = None
                async with self.lock:
                    await self._reset_driver()
                ctx = await self.context()
                return await ctx.new_page()
        except (PWError, RuntimeError) as e:
            raise BrowserUnavailable(str(e).splitlines()[0][:300]) from e

    async def open_for_user(self, url: str) -> str:
        """Open a visible tab in AutoApply's own browser window (e.g. to sign into Handshake). Returns the browser name."""
        if self.headless:
            self.force_headed = True
        page = await self.new_page()
        try:
            await page.goto(url, wait_until="domcontentloaded", timeout=45000)
        except PWError as e:
            log.info("open_for_user: page load issue (window is open anyway): %s", e)
        await page.bring_to_front()
        return self.name

    async def render_pdf(self, html: str, path: Path) -> Path:
        """PDF printing needs headless Chromium, so use a short-lived separate instance."""
        pw = await self._playwright()
        b = await pw.chromium.launch(headless=True)
        try:
            pg = await b.new_page()
            await pg.set_content(html, wait_until="load")
            await pg.pdf(path=str(path), format="Letter", print_background=True)
        finally:
            await b.close()
        return path

    @property
    def running(self) -> bool:
        return self.ctx is not None


browser = Browser()
