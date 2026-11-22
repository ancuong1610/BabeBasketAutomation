"""Publish approved photos and captions through the Meta Graph API."""

from __future__ import annotations

import json
import os
import sys
import time
import urllib.error
import urllib.parse
from functools import partial

from babebasket import config
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


def _instagram_auth() -> tuple[str, ...]:
    """Instagram Login (own token, no Facebook Page needed) when INSTAGRAM_ACCESS_TOKEN is set,
    else Facebook Login through the linked Page's token."""
    config.load_dotenv()
    own = os.environ.get("INSTAGRAM_ACCESS_TOKEN")
    if own:
        return (own,)  # host is passed per call below
    return (config.secret("META_PAGE_ACCESS_TOKEN"),)


def _instagram_host() -> str:
    config.load_dotenv()
    return "graph.instagram.com" if os.environ.get("INSTAGRAM_ACCESS_TOKEN") else "graph.facebook.com"


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
