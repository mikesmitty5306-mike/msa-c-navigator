"""Minimal stdlib HTTP client with per-host throttling and retry/backoff."""

from __future__ import annotations

import json
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

from . import config

USER_AGENT = "msac-research-navigator/0.1 (research aggregation; python-urllib)"

_MIN_INTERVAL = {
    # NCBI allows 3 req/s without a key, 10 with one.
    "eutils.ncbi.nlm.nih.gov": 0.11 if config.NCBI_API_KEY else 0.35,
    "www.ebi.ac.uk": 0.15,
    "clinicaltrials.gov": 0.25,
}
_RETRY_STATUS = {429, 500, 502, 503, 504}
_last_request: dict[str, float] = {}
_lock = threading.Lock()


class FetchError(RuntimeError):
    """Raised when a remote source cannot be reached after retries."""


def _throttle(host: str) -> None:
    interval = _MIN_INTERVAL.get(host, 0.25)
    with _lock:
        wait = _last_request.get(host, 0.0) + interval - time.monotonic()
        if wait > 0:
            time.sleep(wait)
        _last_request[host] = time.monotonic()


def get(url: str, params: dict[str, Any] | None = None, *, timeout: float = 60, retries: int = 4) -> bytes:
    if params:
        url = f"{url}?{urllib.parse.urlencode(params, doseq=True)}"
    host = urllib.parse.urlsplit(url).hostname or ""
    for attempt in range(retries + 1):
        _throttle(host)
        req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return resp.read()
        except urllib.error.HTTPError as exc:
            if exc.code in _RETRY_STATUS and attempt < retries:
                retry_after = exc.headers.get("Retry-After", "")
                time.sleep(float(retry_after) if retry_after.isdigit() else 2 ** attempt)
                continue
            raise FetchError(f"HTTP {exc.code} from {host}") from exc
        except (urllib.error.URLError, TimeoutError, ConnectionError) as exc:
            if attempt < retries:
                time.sleep(2 ** attempt)
                continue
            raise FetchError(f"Network error contacting {host}: {exc}") from exc
    raise FetchError(f"Exhausted retries for {host}")  # pragma: no cover


def get_json(url: str, params: dict[str, Any] | None = None, **kwargs: Any) -> Any:
    return json.loads(get(url, params, **kwargs))
