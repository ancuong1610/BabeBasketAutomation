"""Make one image on gemini.google.com in real Chrome, with the owner's signed-in Gemini account.

Alternative to the paid OpenRouter image model ([image] backend = "gemini_web" in config.toml).
Playwright drives Google Chrome (channel="chrome", visible window) with its own profile in
~/.babebasket/gemini-profile. The owner signs in there once, by hand:

    python3 -m babebasket gemini-login     # plain Chrome on that profile; sign in, then Cmd+Q

Every image is a new Gemini chat in "Create image" mode at 3:4; the first image Gemini returns
is taken, downloaded with page.expect_download() straight into memory (never via ~/Downloads).

Safety: a sign-in page, captcha or "unusual traffic" stops the backend. A marker file is
written and the owner is told on Discord once; until `gemini-login` is run again, every call
returns None at once (so the pipeline uses the plain background). At most `daily_cap` images
a day (default 30), with a random 5-10 s gap between images, counted across runs.
"""

from __future__ import annotations

import json
import os
import random
import re
import subprocess
import sys
import tempfile
import time
from datetime import date
from pathlib import Path

HOME = Path.home() / ".babebasket"
PROFILE = HOME / "gemini-profile"
COUNTER = HOME / "gemini-daily-count.json"
STOPPED = HOME / "gemini-stopped"
URL = "https://gemini.google.com/app"
CHROME = "/Applications/Google Chrome.app"


class GeminiStop(RuntimeError):
    """Sign-in page, captcha or abuse warning: stop and tell the owner."""


class GeminiCap(RuntimeError):
    """Daily image cap reached."""


def _cfg(cfg: dict) -> dict:
    g = (cfg.get("image") or {}).get("gemini_web") or {}
    return {"ratio": g.get("ratio", "3:4"), "timeout": int(g.get("timeout", 180)),
            "daily_cap": int(g.get("daily_cap", 30)), "min_gap": float(g.get("min_gap", 5)),
            "max_gap": float(g.get("max_gap", 10))}


# ---------- daily counter and stop marker ----------

def _count() -> dict:
    try:
        data = json.loads(COUNTER.read_text())
    except (OSError, ValueError):
        data = {}
    if data.get("date") != date.today().isoformat():
        data = {"date": date.today().isoformat(), "count": 0, "last": data.get("last", 0)}
    return data


def _save_count(data: dict) -> None:
    HOME.mkdir(parents=True, exist_ok=True)
    COUNTER.write_text(json.dumps(data))


def stopped() -> str | None:
    try:
        return STOPPED.read_text().strip() or "stopped"
    except OSError:
        return None


def _stop(reason: str) -> None:
    HOME.mkdir(parents=True, exist_ok=True)
    first = not STOPPED.exists()
    STOPPED.write_text(f"{time.strftime('%F %T')} {reason}\n")
    if first:
        _tell_owner(reason)


def _tell_owner(reason: str) -> None:
    try:
        from babebasket import config, discord

        channel = config.load()["discord"]["channel_id"]
        discord._call("POST", f"/channels/{channel}/messages", {
            "content": f"⚠️ Gemini (Chrome) đã dừng: {reason}\nẢnh mới tạm dùng nền trơn. "
                       "Kiểm tra trên Mac mini, rồi chạy `python3 -m babebasket gemini-login` để bật lại.",
            "allowed_mentions": {"parse": []}})
    except Exception as err:  # Discord down must not hide the stop
        print(f"gemini_web: could not notify Discord: {err}", file=sys.stderr)


# ---------- sign-in (done by the owner) ----------

def login() -> None:
    """Open plain Chrome (no automation) on the bot profile so the owner signs in by hand."""
    PROFILE.mkdir(parents=True, exist_ok=True)
    STOPPED.unlink(missing_ok=True)
    subprocess.run(["open", "-na", CHROME, "--args", f"--user-data-dir={PROFILE}",
                    "--no-first-run", "--no-default-browser-check", URL], check=True)
    print(f"Chrome is open on {PROFILE}.\n"
          "Sign in to Gemini there, check that 'Create image' is offered, then quit that Chrome with Cmd+Q.")


# ---------- one image ----------

def _check_page(page) -> None:
    if "accounts.google.com" in page.url:
        raise GeminiStop("Gemini asked to sign in (accounts.google.com)")
    if "/sorry/" in page.url or page.locator("iframe[src*='recaptcha']").count():
        raise GeminiStop("captcha")
    body = (page.locator("body").inner_text(timeout=5000) or "").lower()
    if "unusual traffic" in body or "ungewöhnlichen datenverkehr" in body:
        raise GeminiStop("Google reports unusual traffic")
    if page.locator("a[aria-label^='Sign in'], a[href*='ServiceLogin']").count():
        raise GeminiStop("signed out of Gemini")


def _suffix(data: bytes) -> str:
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return ".png"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return ".webp"
    return ".jpg"


def _run(images: list[bytes], prompt: str, ratio: str, timeout: int) -> bytes:
    from playwright.sync_api import sync_playwright

    if not (PROFILE / "Default").exists():
        raise GeminiStop("no Chrome profile yet: run `python3 -m babebasket gemini-login` and sign in")
    if os.environ.get("SSH_CONNECTION") or os.environ.get("SSH_CLIENT"):
        # Seen 2026-11-22: Chrome started over SSH cannot read the login keychain, finds its
        # cookies unreadable and drops the Google sign-in for good. Only run in the GUI session.
        raise RuntimeError("running over SSH: Chrome must be started from the logged-in GUI session")
    in_use = subprocess.run(["pgrep", "-f", f"user-data-dir={PROFILE}"], capture_output=True).stdout.strip()
    if in_use:
        raise RuntimeError(f"Chrome is still open on {PROFILE}: quit it (Cmd+Q) and try again")
    ms = timeout * 1000
    with tempfile.TemporaryDirectory(prefix="babebasket-gemini-") as tmp, sync_playwright() as pw:
        files = []
        for i, img in enumerate(images):
            p = Path(tmp) / f"input{i}{_suffix(img)}"
            p.write_bytes(img)
            files.append(str(p))
        ctx = pw.chromium.launch_persistent_context(
            str(PROFILE), channel="chrome", headless=False, accept_downloads=True,
            downloads_path=tmp, viewport={"width": 1280, "height": 900},
            # The owner signed in with plain Chrome, whose cookies are locked with the real
            # macOS keychain: Playwright's mock keychain would make the profile look signed out.
            ignore_default_args=["--use-mock-keychain", "--enable-automation"],
            args=["--disable-blink-features=AutomationControlled", "--no-first-run"])
        try:
            page = ctx.pages[0] if ctx.pages else ctx.new_page()
            page.set_default_timeout(30_000)
            page.goto(URL, wait_until="domcontentloaded")  # /app = a new chat every time
            page.wait_for_timeout(2500)
            _check_page(page)

            tools = page.locator('button[aria-label="Upload and tools"]')
            tools.wait_for(state="visible")
            tools.click()
            # Verified 2026-11-22: gem-menu-item, role menuitemcheckbox for the tools.
            page.locator("gem-menu-item").filter(has_text=re.compile(r"^\s*Create image\s*$")).first.click()
            page.locator('button[aria-label="Deselect Images"]').wait_for(state="visible")

            tools.click()
            with page.expect_file_chooser() as chooser:
                page.locator("gem-menu-item").filter(has_text="Upload files").first.click()
            chooser.value.set_files(files)
            page.wait_for_function(
                "n => document.querySelectorAll('img[src^=\"blob:\"]').length >= n", arg=len(files),
                timeout=60_000)

            if ratio:
                page.get_by_role("button", name=re.compile(r"Aspect ratio", re.I)).first.click()
                page.get_by_role("menuitemradio", name=ratio).first.click()

            box = page.locator('[aria-label="Enter a prompt for Gemini"]')
            box.click()
            box.fill(prompt)
            page.locator('button[aria-label="Send message"]').click()

            download_btn = page.locator('model-response button[aria-label="Download full-sized image"]').first
            deadline = time.time() + timeout
            while True:
                try:
                    download_btn.wait_for(state="attached", timeout=5000)
                    break
                except Exception:
                    _check_page(page)
                    if time.time() > deadline:
                        text = page.locator("model-response").last.inner_text(timeout=3000) \
                            if page.locator("model-response").count() else ""
                        raise RuntimeError(f"no image after {timeout}s: {text[:200]!r}")
            # Verified 2026-11-22: clicking as soon as the button is attached does nothing; wait,
            # hover the image, then a normal click. Gemini fetches the full-size file through a few
            # redirects first, so the download event can take 30+ s.
            download_btn.wait_for(state="visible", timeout=30_000)
            page.wait_for_timeout(3000)
            page.locator("model-response img").last.hover()
            page.wait_for_timeout(800)
            with page.expect_download(timeout=ms) as dl:
                download_btn.click()
            out = Path(tmp) / "result"
            dl.value.save_as(out)
            data = out.read_bytes()
            if len(data) < 10_000:
                raise RuntimeError(f"download is only {len(data)} bytes")
            return data
        finally:
            ctx.close()


def generate_image(images: list[bytes], prompt: str, cfg: dict) -> bytes | None:
    """One Gemini web image from the attached images + prompt. None on any failure."""
    g = _cfg(cfg)
    reason = stopped()
    if reason:
        print(f"gemini_web: stopped ({reason}); run `python3 -m babebasket gemini-login`", file=sys.stderr)
        return None
    count = _count()
    if count["count"] >= g["daily_cap"]:
        print(f"gemini_web: daily cap of {g['daily_cap']} images reached", file=sys.stderr)
        return None
    wait = random.uniform(g["min_gap"], g["max_gap"]) - (time.time() - count.get("last", 0))
    if wait > 0:
        time.sleep(wait)
    count["count"] += 1  # counted before the attempt: a failed try still used Gemini
    count["last"] = time.time()
    _save_count(count)
    try:
        return _run(images, prompt, g["ratio"], g["timeout"])
    except GeminiStop as err:
        _stop(str(err))
        print(f"gemini_web: STOP: {err}", file=sys.stderr)
    except Exception as err:
        print(f"gemini_web: failed: {type(err).__name__}: {str(err)[:300]}", file=sys.stderr)
    finally:
        count = _count()
        count["last"] = time.time()
        _save_count(count)
    return None
