"""Small JSON-over-HTTP helper (standard library only)."""

from __future__ import annotations

import gzip
import json
import sys
import time
import urllib.error
import urllib.request

USER_AGENT = "babebasket-research/0.1 (daily product research, one slow run per day)"


def request_json(
    url: str,
    *,
    method: str = "GET",
    headers: dict | None = None,
    body: dict | None = None,
    retries: int = 4,
    timeout: float = 60,
):
    """Send a request and decode the JSON reply. Retries on HTTP 429 and 5xx with backoff."""
    all_headers = {"User-Agent": USER_AGENT, "Accept": "application/json", "Accept-Encoding": "gzip"}
    all_headers.update(headers or {})
    data = None
    if body is not None:
        data = json.dumps(body).encode()
        all_headers["Content-Type"] = "application/json"

    backoff = 15.0
    for attempt in range(retries + 1):
        req = urllib.request.Request(url, data=data, headers=all_headers, method=method)
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                raw = resp.read()
                if raw[:2] == b"\x1f\x8b":
                    raw = gzip.decompress(raw)
                return json.loads(raw) if raw else None
        except urllib.error.HTTPError as err:
            retryable = err.code == 429 or err.code >= 500
            if retryable and attempt < retries:
                wait = backoff
                retry_after = err.headers.get("Retry-After")
                if retry_after:
                    try:
                        wait = max(wait, float(retry_after))
                    except ValueError:
                        pass
                print(f"{err.code} from {url.split('?')[0]}, retrying in {wait:.0f}s", file=sys.stderr)
                time.sleep(wait)
                backoff *= 2
                continue
            detail = err.read()[:500].decode(errors="replace")
            raise RuntimeError(f"HTTP {err.code} for {method} {url.split('?')[0]}: {detail}") from err
    raise RuntimeError("unreachable")


def encode_multipart(fields: dict[str, str], files: list[tuple[str, str, bytes, str]]) -> tuple[bytes, str]:
    """multipart/form-data body. files: (field name, filename, data, content type)."""
    import uuid

    boundary = "babebasket-" + uuid.uuid4().hex
    out = bytearray()
    for name, value in fields.items():
        out += f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"\r\n'.encode()
        out += b"Content-Type: application/json\r\n\r\n" if name == "payload_json" else b"\r\n"
        out += value.encode() + b"\r\n"
    for name, filename, data, ctype in files:
        out += f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"; filename="{filename}"\r\n'.encode()
        out += f"Content-Type: {ctype}\r\n\r\n".encode() + data + b"\r\n"
    out += f"--{boundary}--\r\n".encode()
    return bytes(out), f"multipart/form-data; boundary={boundary}"


def request_multipart(url: str, *, fields: dict[str, str], files: list[tuple[str, str, bytes, str]],
                      headers: dict | None = None, timeout: float = 60):
    """POST a multipart form and decode the JSON reply (no retries: uploads are not idempotent)."""
    body, ctype = encode_multipart(fields, files)
    all_headers = {"User-Agent": USER_AGENT, "Accept": "application/json", "Content-Type": ctype}
    all_headers.update(headers or {})
    req = urllib.request.Request(url, data=body, headers=all_headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read()
            return json.loads(raw) if raw else None
    except urllib.error.HTTPError as err:
        detail = err.read()[:500].decode(errors="replace")
        raise RuntimeError(f"HTTP {err.code} for POST {url.split('?')[0]}: {detail}") from err
