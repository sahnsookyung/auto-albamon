"""Bounded, polite HTTP reads for a fixed allowlist of public job boards."""

from __future__ import annotations

import time
import shutil
import subprocess
import urllib.error
import urllib.parse
import urllib.request
from typing import Callable


ALLOWED_HOSTS = {"with-you.kr", "www.huplux.com", "www.eumplus.co.kr"}
HOST_INTERVAL_SECONDS = {"www.huplux.com": 5.0}


class SourceFetchError(RuntimeError):
    pass


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

    def get(self, url: str) -> str:
        self._validate_url(url)
        host = urllib.parse.urlparse(url).hostname or ""
        interval = HOST_INTERVAL_SECONDS.get(host, self.minimum_interval_seconds)
        delay = interval - (
            self._monotonic() - self._last_request_at_by_host.get(host, 0.0)
        )
        if delay > 0:
            self._sleeper(delay)
        self._last_request_at_by_host[host] = self._monotonic()
        curl_path = shutil.which("curl")
        if curl_path:
            return self._curl_get(curl_path, url)
        return self._urllib_get(url)

    def _curl_get(self, curl_path: str, url: str) -> str:
        """Use the OS trust store; WithYou serves a chain rejected by pyenv SSL."""

        host = urllib.parse.urlparse(url).hostname or ""
        effective_timeout = (
            min(8, self.timeout_seconds)
            if host == "www.huplux.com"
            else self.timeout_seconds
        )
        try:
            completed = subprocess.run(
                [
                    curl_path,
                    "--fail",
                    "--silent",
                    "--show-error",
                    "--connect-timeout",
                    str(min(5, effective_timeout)),
                    "--max-time",
                    str(effective_timeout),
                    "--max-filesize",
                    str(self.max_response_bytes),
                    "--retry",
                    "1",
                    "--retry-delay",
                    "1",
                    "--retry-all-errors",
                    "--proto",
                    "=http,https",
                    "--proto-redir",
                    "=http,https",
                    "--user-agent",
                    "AutoAlbamon-WeekendAgencyReport/1.0",
                    url,
                ],
                check=True,
                capture_output=True,
                timeout=effective_timeout * 2 + 3,
            )
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired, OSError) as exc:
            raise SourceFetchError(
                f"source request failed via verified system HTTP client: {type(exc).__name__}"
            ) from exc
        if len(completed.stdout) > self.max_response_bytes:
            raise SourceFetchError("source response exceeded the size limit")
        return completed.stdout.decode("utf-8", errors="replace")

    def _urllib_get(self, url: str) -> str:
        request = urllib.request.Request(
            url,
            headers={
                "User-Agent": "AutoAlbamon-WeekendAgencyReport/1.0",
                "Accept": "text/html,application/xhtml+xml",
            },
        )
        last_error: Exception | None = None
        for attempt in range(2):
            try:
                with urllib.request.urlopen(
                    request, timeout=self.timeout_seconds
                ) as response:
                    payload = response.read(self.max_response_bytes + 1)
                    if len(payload) > self.max_response_bytes:
                        raise SourceFetchError("source response exceeded the size limit")
                    charset = response.headers.get_content_charset() or "utf-8"
                    return payload.decode(charset, errors="replace")
            except (urllib.error.URLError, TimeoutError, OSError) as exc:
                last_error = exc
                if attempt == 0:
                    self._sleeper(0.5)
        raise SourceFetchError(f"source request failed after one retry: {last_error}")
