"""Hardened JSON POST for provider calls.

- explicit connect and read timeouts; TLS verification always on
- response body capped at 64 KiB (read incrementally, never trusted to be small)
- exceptions split by whether the request can have reached the provider:
  a failure to connect is safe to retry; a timeout or reset after the request
  was sent is ambiguous and must not be blindly resent
- nothing here logs headers, bodies or URLs with secrets in them
"""
from __future__ import annotations

import json
from dataclasses import dataclass

import requests
from urllib3.exceptions import NameResolutionError, NewConnectionError

MAX_RESPONSE_BYTES = 64 * 1024

CONNECT_FAILED = 'connect_failed'
READ_TIMEOUT = 'read_timeout'
CONNECTION_LOST = 'connection_lost'
TOO_LARGE = 'too_large'


@dataclass(frozen=True)
class HttpOutcome:
    status_code: int | None = None
    body: dict | list | None = None
    json_ok: bool = False
    transport_error: str = ''


def _never_connected(exc: requests.exceptions.ConnectionError) -> bool:
    """True when the TCP/TLS connection itself failed (no request bytes sent)."""
    if isinstance(exc, (requests.exceptions.ConnectTimeout, requests.exceptions.SSLError,
                        requests.exceptions.ProxyError)):
        return True
    seen = set()
    stack = [exc]
    while stack:
        current = stack.pop()
        if current is None or id(current) in seen:
            continue
        seen.add(id(current))
        if isinstance(current, (NewConnectionError, NameResolutionError, ConnectionRefusedError)):
            return True
        stack.extend([getattr(current, 'reason', None), current.__cause__, current.__context__])
        stack.extend(a for a in getattr(current, 'args', ()) if isinstance(a, BaseException))
    return False


def post_json(url: str, *, headers: dict, payload: dict, timeout: tuple[float, float]) -> HttpOutcome:
    try:
        response = requests.post(
            url,
            headers={**headers, 'Content-Type': 'application/json', 'Accept': 'application/json'},
            data=json.dumps(payload),
            timeout=timeout,
            verify=True,
            stream=True,
            allow_redirects=False,
        )
    except requests.exceptions.ConnectionError as exc:
        return HttpOutcome(transport_error=CONNECT_FAILED if _never_connected(exc) else CONNECTION_LOST)
    except requests.exceptions.Timeout:
        # ConnectTimeout is a ConnectionError and handled above; this is a
        # read timeout, i.e. the request was sent and the answer is unknown.
        return HttpOutcome(transport_error=READ_TIMEOUT)
    except requests.exceptions.RequestException:
        return HttpOutcome(transport_error=CONNECTION_LOST)

    try:
        raw = response.raw.read(MAX_RESPONSE_BYTES + 1, decode_content=True)
    except requests.exceptions.RequestException:
        return HttpOutcome(status_code=response.status_code, transport_error=CONNECTION_LOST)
    except Exception:
        return HttpOutcome(status_code=response.status_code, transport_error=CONNECTION_LOST)
    finally:
        response.close()
    if len(raw) > MAX_RESPONSE_BYTES:
        return HttpOutcome(status_code=response.status_code, transport_error=TOO_LARGE)
    try:
        body = json.loads(raw.decode('utf-8')) if raw else None
        return HttpOutcome(status_code=response.status_code, body=body, json_ok=body is not None)
    except (ValueError, UnicodeDecodeError):
        return HttpOutcome(status_code=response.status_code)
