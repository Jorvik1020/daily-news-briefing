"""Network resilience helpers for launchd-scheduled jobs.

launchd fires a missed StartCalendarInterval job immediately on wake — often
before Wi-Fi/DNS is back up, so the job's first network call dies with
'nodename nor servname' (DNS) or a dropped connection. These helpers let a job
wait for connectivity at startup and retry transient network failures instead
of crashing. Dependency-free (stdlib only) so they run under launchd."""
from __future__ import annotations

import socket
import sys
import time
import urllib.error

# Resolving ANY of these means DNS/network is up. Covers the LLM gateway and
# the Telegram egress that every scheduled job ultimately needs.
_PROBE_HOSTS = ("api.anthropic.com", "api.telegram.org")

# litellm/urllib error class names worth retrying. Matched by name so this module
# stays import-light (no litellm dependency just to classify an exception).
_TRANSIENT_NAMES = frozenset({
    "APIConnectionError", "APITimeoutError", "InternalServerError",
    "ServiceUnavailableError", "Timeout", "RateLimitError",
})


def wait_for_network(timeout: float = 90.0, interval: float = 3.0,
                     hosts: tuple[str, ...] = _PROBE_HOSTS) -> bool:
    """Block until DNS resolves for any probe host, or `timeout` seconds pass.

    Returns True as soon as the network looks usable, False if it never came up.
    A False return does NOT abort the caller — a real outage must not hang the
    job forever, and the actual calls are still guarded by retry()/try-except.
    The point is purely to absorb the few seconds between wake and Wi-Fi."""
    deadline = time.monotonic() + timeout
    attempt = 0
    while True:
        for host in hosts:
            try:
                socket.getaddrinfo(host, 443, proto=socket.IPPROTO_TCP)
                if attempt:
                    print(f"[net] connectivity up after {attempt} retr"
                          f"{'y' if attempt == 1 else 'ies'}.", file=sys.stderr)
                return True
            except OSError:
                continue
        if time.monotonic() >= deadline:
            print(f"[net] network still down after {timeout:.0f}s — proceeding "
                  f"anyway (calls will retry / fail soft).", file=sys.stderr)
            return False
        attempt += 1
        time.sleep(interval)


def is_transient(exc: BaseException) -> bool:
    """True for errors worth retrying: DNS/socket failures, dropped connections,
    and litellm's transient server/connection/timeout errors. Deliberately does
    NOT match generic Exception/RuntimeError — those should surface immediately."""
    if isinstance(exc, (socket.gaierror, socket.timeout, ConnectionError,
                        TimeoutError, urllib.error.URLError)):
        return True
    return type(exc).__name__ in _TRANSIENT_NAMES


def retry(fn, *, tries: int = 4, base_delay: float = 2.0, factor: float = 2.0,
          label: str = "call"):
    """Call fn(); on a TRANSIENT failure retry with exponential backoff.

    Re-raises a non-transient error immediately, and re-raises the last error
    after `tries` attempts. Delay grows base_delay * factor**n between tries."""
    delay = base_delay
    for n in range(1, tries + 1):
        try:
            return fn()
        except Exception as e:  # noqa: BLE001 - classify, then retry or re-raise
            if not is_transient(e) or n == tries:
                raise
            print(f"[net] {label}: transient failure {n}/{tries} ({e}); "
                  f"retrying in {delay:.0f}s.", file=sys.stderr)
            time.sleep(delay)
            delay *= factor
