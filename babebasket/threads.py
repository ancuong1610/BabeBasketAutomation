"""Publish to Threads via the Threads Graph API.

Flow: create a media container (IMAGE or TEXT) -> wait until it is FINISHED -> publish -> read the permalink.
The image must be at a public URL; we pass the Discord CDN link of the approval card's post.jpg.
The token only ever travels in query strings, and request_json strips those from error messages.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import os
import sys
import time
import urllib.parse

from babebasket.config import DATA, load_dotenv, secret
from babebasket.http import request_json

API = "https://graph.threads.net/v1.0/"
TOKEN_FILE = DATA / "threads_token.json"
MAX_CHARS = 500
REFRESH_AFTER_DAYS = 7


def _fingerprint(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()[:16]


def _saved() -> dict:
    """The refreshed token, but only while it still descends from the token in .env (a new .env token wins)."""
    if not TOKEN_FILE.exists():
        return {}
    saved = json.loads(TOKEN_FILE.read_text())
    load_dotenv()
    env = os.environ.get("THREADS_ACCESS_TOKEN") or ""
    return saved if saved.get("source") == _fingerprint(env) else {}


def token() -> str:
    return _saved().get("access_token") or secret("THREADS_ACCESS_TOKEN")


def _url(path: str, **params) -> str:
    params["access_token"] = token()
    return API + path + "?" + urllib.parse.urlencode({k: v for k, v in params.items() if v is not None})


MAX_CAROUSEL = 10  # we post at most 10 photos (Threads allows more); the AI image always comes first


def _wait(container: str, wait_s: int) -> None:
    for _ in range(max(1, wait_s // 5)):
        state = request_json(_url(container, fields="status,error_message"))
        if state.get("status") == "FINISHED":
            return
        if state.get("status") in ("ERROR", "EXPIRED"):
            raise RuntimeError(f"Threads container {state.get('status')}: {state.get('error_message')}")
        time.sleep(5)
    raise RuntimeError("Threads container not ready in time")


def publish(text: str, image_url: str | None = None, wait_s: int = 60, extra_images: list[str] | None = None) -> dict:
    """Post text with one image, or a carousel when `extra_images` are given (image_url first)."""
    uid = request_json(_url("me", fields="id"))["id"]
    images = ([image_url] if image_url else []) + list(extra_images or [])
    images = images[:MAX_CAROUSEL]
    # Creating containers publishes nothing, so a retry here is harmless.
    if len(images) > 1:
        children = []
        for url in images:
            child = request_json(_url(f"{uid}/threads", media_type="IMAGE", image_url=url, is_carousel_item="true"),
                                 method="POST")["id"]
            _wait(child, wait_s)
            children.append(child)
        container = request_json(_url(f"{uid}/threads", media_type="CAROUSEL", children=",".join(children),
                                      text=text), method="POST")["id"]
    else:
        media_type = "IMAGE" if image_url else "TEXT"
        container = request_json(_url(f"{uid}/threads", media_type=media_type, text=text, image_url=image_url),
                                 method="POST")["id"]
    _wait(container, wait_s)
    # Never retried automatically: a retry after a lost reply could post twice.
    media = request_json(_url(f"{uid}/threads_publish", creation_id=container), method="POST", retries=0)["id"]
    try:  # the post is live already: a failed link lookup must not make the caller post again
        link = request_json(_url(media, fields="permalink")).get("permalink", "")
    except Exception as err:
        print(f"! threads permalink lookup failed for {media}: {err}", file=sys.stderr)
        link = ""
    return {"id": media, "permalink": link}


def _save(access_token: str, source: str, expires_in=None) -> None:
    TOKEN_FILE.parent.mkdir(parents=True, exist_ok=True)
    TOKEN_FILE.write_text(json.dumps({
        "access_token": access_token, "source": source, "expires_in": expires_in,
        "refreshed_at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
    }))
    os.chmod(TOKEN_FILE, 0o600)


def maybe_refresh() -> bool:
    """Swap the long-lived token for a fresh 60-day one once it is a week old. Returns True if refreshed."""
    env = secret("THREADS_ACCESS_TOKEN")
    saved = _saved()
    if not saved:  # first run or a new token in .env: start the clock, no call (tokens < 24 h can't refresh)
        _save(env, _fingerprint(env))
        return False
    age = dt.datetime.now(dt.timezone.utc) - dt.datetime.fromisoformat(saved["refreshed_at"])
    if age < dt.timedelta(days=REFRESH_AFTER_DAYS):
        return False
    reply = request_json(API + "refresh_access_token?" + urllib.parse.urlencode(
        {"grant_type": "th_refresh_token", "access_token": saved["access_token"]}))
    _save(reply["access_token"], saved["source"], reply.get("expires_in"))
    return True
