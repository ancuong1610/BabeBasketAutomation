"""Publish approved photos and captions through the Meta Graph API."""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import os
import sys
import time
import urllib.error
import urllib.parse
from functools import partial

from babebasket import config
from babebasket.config import DATA
from babebasket.http import request_json, request_multipart

DEFAULT_GRAPH_VERSION = "v21.0"
INSTAGRAM_MAX_CHARS = 2200
FACEBOOK_MAX_CHARS = 60000
INSTAGRAM_MAX_IMAGES = 10


def _request(graph_version: str, token: str, path: str, *, method="GET", retries=4, timeout=60,
             host: str = "graph.facebook.com", **params):
    endpoint = f"https://{host}/{graph_version}/{path}"
    auth = urllib.parse.urlencode({"access_token": token})
    url = f"{endpoint}?{urllib.parse.urlencode(params)}&{auth}"
    try:
        if method == "POST" and len(url) > 8000:
            # A caption can fit the platform's limit and still make the URL too long.
            return request_multipart(f"{endpoint}?{auth}", fields=params, files=[], retries=retries, timeout=timeout)
        return request_json(url, method=method, retries=retries, timeout=timeout)
    except Exception as err:
        # API error bodies and transport exceptions can include the request or its parameters.
        http_error = err if isinstance(err, urllib.error.HTTPError) else err.__cause__
        reason = f"HTTP {http_error.code}" if isinstance(http_error, urllib.error.HTTPError) else type(err).__name__
        raise RuntimeError(f"Meta {method} {path} failed ({reason})") from None


def _wait_for_instagram(request, container: str, wait_s: int) -> None:
    deadline = time.monotonic() + wait_s
    while True:
        remaining = deadline - time.monotonic()
        status = request(container, fields="status_code", retries=0, timeout=max(1, min(60, remaining)))
        code = status.get("status_code")
        if code == "FINISHED":
            return
        if code in ("ERROR", "EXPIRED"):
            raise RuntimeError(f"Instagram container {code}")
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            break
        time.sleep(min(5, remaining))
        if time.monotonic() >= deadline:
            break
    raise RuntimeError("Instagram container not ready in time")


def _post_result(request, platform: str, post_id: str, field: str) -> dict:
    try:
        permalink = request(post_id, fields=field, retries=0).get(field) or ""
    except Exception as err:
        print(f"! {platform} permalink lookup failed for {post_id}: {err}", file=sys.stderr)
        permalink = ""
    return {"id": post_id, "permalink": permalink}


# --- Instagram token: kept fresh like the Threads token -------------------------------------------
# The token pasted into .env starts a 60-day clock. Once it is a week old it is swapped for a fresh one and
# saved in data/instagram_token.json (0600). A new token pasted into .env always wins over the saved one.
#   "IG…" (Instagram Login)        -> graph.instagram.com/refresh_access_token (no app secret needed)
#   "EAA…" (Facebook-type, Unified login) -> oauth/access_token fb_exchange_token (needs META_APP_ID + META_APP_SECRET)
TOKEN_FILE = DATA / "instagram_token.json"
REFRESH_AFTER_DAYS = 7


def _fingerprint(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()[:16]


def _env_instagram_token() -> str:
    config.load_dotenv()
    return os.environ.get("INSTAGRAM_ACCESS_TOKEN") or ""


def _saved_instagram() -> dict:
    env = _env_instagram_token()
    if not env or not TOKEN_FILE.exists():
        return {}
    try:
        saved = json.loads(TOKEN_FILE.read_text())
    except (OSError, ValueError):
        return {}
    return saved if saved.get("source") == _fingerprint(env) else {}


def instagram_token() -> str:
    return _saved_instagram().get("access_token") or _env_instagram_token()


def _never_expires(token: str, graph_version: str) -> bool:
    """debug_token with the app token: expires_at 0 means the token never expires. Needs META_APP_ID/SECRET."""
    config.load_dotenv()
    app_id, app_secret = os.environ.get("META_APP_ID"), os.environ.get("META_APP_SECRET")
    if not (app_id and app_secret):
        return False
    try:
        data = request_json(f"https://graph.facebook.com/{graph_version}/debug_token?" + urllib.parse.urlencode(
            {"input_token": token, "access_token": f"{app_id}|{app_secret}"}), retries=1).get("data") or {}
    except Exception as err:
        raise RuntimeError(f"Instagram token check failed ({type(err).__name__})") from None
    return data.get("is_valid") is True and data.get("expires_at") == 0


def _save_instagram(token: str, source: str, expires_in=None, never_expires: bool = False) -> None:
    now = dt.datetime.now(dt.timezone.utc)
    TOKEN_FILE.parent.mkdir(parents=True, exist_ok=True)
    TOKEN_FILE.write_text(json.dumps({
        "access_token": token, "source": source, "refreshed_at": now.isoformat(timespec="seconds"),
        "never_expires": never_expires,
        "expires_at": (now + dt.timedelta(seconds=int(expires_in))).isoformat(timespec="seconds")
        if expires_in else None,
    }))
    os.chmod(TOKEN_FILE, 0o600)


def maybe_refresh_instagram(graph_version: str = DEFAULT_GRAPH_VERSION) -> bool:
    """Swap the Instagram token for a fresh 60-day one once it is a week old. True if refreshed."""
    env = _env_instagram_token()
    if not env:  # Page-token setup (META_PAGE_ACCESS_TOKEN): Page tokens from a long-lived user token do not expire
        return False
    saved = _saved_instagram()
    if not saved:  # first run or a new token in .env: start the clock, no call
        _save_instagram(env, _fingerprint(env))
        return False
    age = dt.datetime.now(dt.timezone.utc) - dt.datetime.fromisoformat(saved["refreshed_at"])
    if age < dt.timedelta(days=REFRESH_AFTER_DAYS):
        return False
    current = saved["access_token"]
    if saved.get("never_expires"):
        return False
    if not current.startswith("IG") and _never_expires(current, graph_version):
        # System-user tokens (Business portfolio) never expire; exchanging one would swap it for a 60-day token.
        _save_instagram(current, saved["source"], never_expires=True)
        return False
    if current.startswith("IG"):
        reply = request_json("https://graph.instagram.com/refresh_access_token?" + urllib.parse.urlencode(
            {"grant_type": "ig_refresh_token", "access_token": current}), retries=1)
    else:
        config.load_dotenv()
        app_id, app_secret = os.environ.get("META_APP_ID"), os.environ.get("META_APP_SECRET")
        if not (app_id and app_secret):
            raise RuntimeError("Instagram token cannot be renewed: add META_APP_ID and META_APP_SECRET to .env "
                               "(developers.facebook.com → app → App settings → Basic)")
        try:
            reply = request_json(f"https://graph.facebook.com/{graph_version}/oauth/access_token?" + urllib.parse.urlencode(
                {"grant_type": "fb_exchange_token", "client_id": app_id, "client_secret": app_secret,
                 "fb_exchange_token": current}), retries=1)
        except Exception as err:  # never let the app secret or token reach a log
            raise RuntimeError(f"Instagram token exchange failed ({type(err).__name__})") from None
    _save_instagram(reply["access_token"], saved["source"], reply.get("expires_in"))
    return True


def _instagram_auth() -> tuple[str, ...]:
    """The Instagram token (kept fresh, see above), else the linked Facebook Page's token."""
    own = instagram_token()
    if own:
        return (own,)  # host is passed per call below
    return (config.secret("META_PAGE_ACCESS_TOKEN"),)


def _instagram_host() -> str:
    """Instagram Login tokens ("IG…") use graph.instagram.com; Facebook-type tokens ("EAA…", from the
    dashboard's Unified login / Facebook Login for Business, or a Page token) use graph.facebook.com."""
    return "graph.instagram.com" if instagram_token().startswith("IG") else "graph.facebook.com"


def instagram_publish(caption: str, image_url: str | None, extra_images: list[str] | None = None, wait_s: int = 60,
                      *, graph_version: str = DEFAULT_GRAPH_VERSION) -> dict:
    if not image_url:
        raise ValueError("Instagram needs an image")
    if len(caption) > INSTAGRAM_MAX_CHARS:
        raise ValueError(f"Instagram allows {INSTAGRAM_MAX_CHARS} characters")
    user_id = config.secret("INSTAGRAM_USER_ID")
    request = partial(_request, graph_version, *_instagram_auth(), host=_instagram_host())
    images = [image_url, *(extra_images or [])][:INSTAGRAM_MAX_IMAGES]
    media_path = f"{user_id}/media"
    if len(images) == 1:
        container = request(media_path, method="POST", image_url=image_url, caption=caption)["id"]
    else:
        children = [request(media_path, method="POST", image_url=url, is_carousel_item="true")["id"]
                    for url in images]
        container = request(media_path, method="POST", media_type="CAROUSEL",
                            children=",".join(children), caption=caption)["id"]
    _wait_for_instagram(request, container, wait_s)
    media = request(f"{user_id}/media_publish", method="POST", creation_id=container, retries=0)["id"]
    return _post_result(request, "Instagram", media, "permalink")


def facebook_publish(message: str, image_url: str | None = None, extra_images: list[str] | None = None,
                     *, graph_version: str = DEFAULT_GRAPH_VERSION) -> dict:
    if len(message) > FACEBOOK_MAX_CHARS:
        raise ValueError(f"Facebook allows {FACEBOOK_MAX_CHARS} characters")
    page_id = config.secret("FACEBOOK_PAGE_ID")
    request = partial(_request, graph_version, config.secret("META_PAGE_ACCESS_TOKEN"))
    images = ([image_url] if image_url else []) + list(extra_images or [])
    photos_path = f"{page_id}/photos"
    if len(images) == 1:
        photo = request(photos_path, method="POST", url=images[0], caption=message, retries=0)
        post_id = photo.get("post_id") or photo["id"]
    else:
        photo_ids = [request(photos_path, method="POST", url=url, published="false")["id"] for url in images]
        attached = {f"attached_media[{i}]": json.dumps({"media_fbid": photo_id}, separators=(",", ":"))
                    for i, photo_id in enumerate(photo_ids)}
        post_id = request(f"{page_id}/feed", method="POST", message=message, retries=0, **attached)["id"]
    return _post_result(request, "Facebook", post_id, "permalink_url")
