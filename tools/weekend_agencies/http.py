"""Bounded, polite HTTP reads for a fixed allowlist of public job boards."""

from __future__ import annotations

import re
import shutil
import subprocess
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Callable


ALLOWED_HOSTS = {
    "htservice.kr",
    "with-you.kr",
    "www.eumplus.co.kr",
    "www.huplux.com",
    "www.oulim.kr",
    "www.xn--2s2bi09b.com",
}
HOST_INTERVAL_SECONDS = {
    "htservice.kr": 1.0,
    "www.huplux.com": 5.0,
    "www.oulim.kr": 1.0,
    "www.xn--2s2bi09b.com": 1.0,
}
HOST_MAX_ATTEMPTS = {"www.huplux.com": 3}
HOST_CONNECT_TIMEOUT_SECONDS = {"www.huplux.com": 8}
HOST_REQUEST_TIMEOUT_SECONDS = {"www.huplux.com": 15}
DEFAULT_USER_AGENT = "AutoAlbamon-WeekendAgencyReport/1.0"


class SourceFetchError(RuntimeError):
    def __init__(self, message: str, *, retryable: bool = False) -> None:
        super().__init__(message)
        self.retryable = retryable


class HttpClient:
    def __init__(
        self,
        *,
        timeout_seconds: int = 20,
        max_response_bytes: int = 3_000_000,
        minimum_interval_seconds: float = 0.5,
        sleeper: Callable[[float], None] = time.sleep,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        self.timeout_seconds = timeout_seconds
        self.max_response_bytes = max_response_bytes
        self.minimum_interval_seconds = minimum_interval_seconds
        self._sleeper = sleeper
        self._monotonic = monotonic
        self._last_request_at_by_host: dict[str, float] = {}

    def _validate_url(self, url: str) -> None:
        parsed = urllib.parse.urlparse(url)
        if parsed.scheme not in {"http", "https"} or parsed.hostname not in ALLOWED_HOSTS:
            raise ValueError(f"source URL is outside the fixed allowlist: {url}")

    def get(self, url: str, *, encoding: str | None = None) -> str:
        self._validate_url(url)
        host = urllib.parse.urlparse(url).hostname or ""
        curl_path = shutil.which("curl")
        max_attempts = HOST_MAX_ATTEMPTS.get(host, 1)
        last_error: SourceFetchError | None = None
        for attempt in range(1, max_attempts + 1):
            self._pace_request(host)
            try:
                if curl_path:
                    return self._curl_get(curl_path, url, encoding=encoding)
                return self._urllib_get(
                    url,
                    encoding=encoding,
                    max_attempts=1 if max_attempts > 1 else 2,
                )
            except SourceFetchError as exc:
                last_error = exc
                if not exc.retryable or attempt == max_attempts:
                    break
        assert last_error is not None
        if max_attempts == 1:
            raise last_error
        raise SourceFetchError(
            f"{last_error}; failed after {max_attempts} paced attempts",
            retryable=last_error.retryable,
        ) from last_error

    def _pace_request(self, host: str) -> None:
        interval = HOST_INTERVAL_SECONDS.get(host, self.minimum_interval_seconds)
        delay = interval - (
            self._monotonic() - self._last_request_at_by_host.get(host, 0.0)
        )
        if delay > 0:
            self._sleeper(delay)
        self._last_request_at_by_host[host] = self._monotonic()

    def _curl_get(
        self, curl_path: str, url: str, *, encoding: str | None = None
    ) -> str:
        """Use the OS trust store; WithYou serves a chain rejected by pyenv SSL."""

        host = urllib.parse.urlparse(url).hostname or ""
        effective_timeout = min(
            self.timeout_seconds,
            HOST_REQUEST_TIMEOUT_SECONDS.get(host, self.timeout_seconds),
        )
        connect_timeout = min(
            effective_timeout,
            HOST_CONNECT_TIMEOUT_SECONDS.get(host, 5),
        )
        curl_retries = "0" if host in HOST_MAX_ATTEMPTS else "1"
        try:
            completed = subprocess.run(
                [
                    curl_path,
                    "--fail",
                    "--silent",
                    "--show-error",
                    "--connect-timeout",
                    str(connect_timeout),
                    "--max-time",
                    str(effective_timeout),
                    "--max-filesize",
                    str(self.max_response_bytes),
                    "--retry",
                    curl_retries,
                    "--retry-delay",
                    "1",
                    "--retry-all-errors",
                    "--proto",
                    "=http,https",
                    "--proto-redir",
                    "=http,https",
                    "--user-agent",
                    DEFAULT_USER_AGENT,
                    url,
                ],
                check=True,
                capture_output=True,
                timeout=effective_timeout * 2 + 3,
            )
        except subprocess.CalledProcessError as exc:
            status_match = re.search(rb"returned error:\s*(\d{3})", exc.stderr or b"")
            if status_match:
                status = int(status_match.group(1))
                retryable = status == 429 or status >= 500
                message = f"source returned HTTP {status}"
            else:
                retryable = exc.returncode in {
                    5,
                    6,
                    7,
                    18,
                    28,
                    35,
                    52,
                    55,
                    56,
                    92,
                }
                category = {
                    5: "proxy resolution failed",
                    6: "DNS resolution failed",
                    7: "connection failed",
                    18: "partial response",
                    28: "transport timeout",
                    35: "TLS handshake failed",
                    52: "empty response",
                    55: "request send failed",
                    56: "response receive failed",
                    92: "HTTP/2 stream failed",
                }.get(exc.returncode, "transport command failed")
                message = f"source {category} (curl exit {exc.returncode})"
            raise SourceFetchError(
                message,
                retryable=retryable,
            ) from exc
        except subprocess.TimeoutExpired as exc:
            raise SourceFetchError(
                "source transport process timed out",
                retryable=True,
            ) from exc
        except OSError as exc:
            raise SourceFetchError(
                "source transport process could not start",
                retryable=False,
            ) from exc
        if len(completed.stdout) > self.max_response_bytes:
            raise SourceFetchError("source response exceeded the size limit")
        return completed.stdout.decode(encoding or "utf-8", errors="replace")

    def _urllib_get(
        self,
        url: str,
        *,
        encoding: str | None = None,
        max_attempts: int = 2,
    ) -> str:
        request = urllib.request.Request(
            url,
            headers={
                "User-Agent": DEFAULT_USER_AGENT,
                "Accept": "text/html,application/xhtml+xml",
            },
        )
        last_error: SourceFetchError | None = None
        for attempt in range(max_attempts):
            try:
                with urllib.request.urlopen(
                    request, timeout=self.timeout_seconds
                ) as response:
                    payload = response.read(self.max_response_bytes + 1)
                    if len(payload) > self.max_response_bytes:
                        raise SourceFetchError("source response exceeded the size limit")
                    charset = encoding or response.headers.get_content_charset() or "utf-8"
                    return payload.decode(charset, errors="replace")
            except urllib.error.HTTPError as exc:
                retryable = exc.code == 429 or exc.code >= 500
                last_error = SourceFetchError(
                    f"source returned HTTP {exc.code}",
                    retryable=retryable,
                )
                if not retryable:
                    raise last_error from exc
            except (urllib.error.URLError, TimeoutError, OSError) as exc:
                last_error = SourceFetchError(
                    f"source transport failed via urllib ({type(exc).__name__})",
                    retryable=True,
                )
            if attempt + 1 < max_attempts:
                self._sleeper(0.5)
        assert last_error is not None
        raise last_error
