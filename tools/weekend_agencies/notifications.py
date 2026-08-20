"""Discord delivery for the isolated weekend hotel-agency report.

This module deliberately owns a webhook variable and notification state that
are separate from Albamon. It baselines a first report with a resumable,
hotel-aggregated digest and then sends only new or materially changed hotels.
"""

from __future__ import annotations

import json
import os
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Callable

try:
    from tools.albamon_runtime import atomic_write_text, exclusive_file_lock
except ModuleNotFoundError:  # Direct CLI execution via tools/.
    from albamon_runtime import atomic_write_text, exclusive_file_lock

from .hotel_digest import (
    DISCORD_MESSAGE_LIMIT,
    aggregate_hotels,
    digest_fingerprint,
    format_complete_digest,
    format_update_digest,
    hotel_fingerprint,
)


WEBHOOK_ENV_NAME = "DISCORD_WEBHOOK_URL_HOTELIER"
DEFAULT_STATE_PATH = Path(
    "artifacts/weekend-agencies/cache/discord_notifications.json"
)
STATE_VERSION = 2
DIGEST_FORMAT_VERSION = 3
STATE_RETENTION_DAYS = 30
DISCORD_WEBHOOK_HOSTS = {
    "discord.com",
    "canary.discord.com",
    "ptb.discord.com",
    "discordapp.com",
}


class HotelierNotificationError(RuntimeError):
    """Raised when hotel-agency delivery cannot complete safely."""


def _now() -> datetime:
    return datetime.now().astimezone()


def _env_file_value(path: Path, name: str) -> str:
    """Read one literal env value without evaluating the file as shell code."""

    if not path.exists():
        return ""
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line.removeprefix("export ").lstrip()
        key, separator, value = line.partition("=")
        if not separator or key.strip() != name:
            continue
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {'"', "'"}:
            value = value[1:-1]
        return value.strip()
    return ""


def validate_webhook_url(url: str) -> str:
    parsed = urllib.parse.urlparse(url)
    host = (parsed.hostname or "").lower()
    if parsed.scheme != "https" or host not in DISCORD_WEBHOOK_HOSTS:
        raise HotelierNotificationError(
            "Hotelier Discord webhook must use HTTPS on an official Discord host"
        )
    if parsed.username or parsed.password:
        raise HotelierNotificationError(
            "Hotelier Discord webhook URL must not contain URL credentials"
        )
    if not re.fullmatch(r"/api(?:/v\d+)?/webhooks/\d+/[^/]+/?", parsed.path):
        raise HotelierNotificationError(
            "Hotelier Discord webhook URL has an unexpected path"
        )
    return url


def resolve_hotelier_webhook(env_file: Path | None) -> str:
    """Resolve only the hotelier destination, never the Albamon webhook."""

    url = (
        _env_file_value(env_file, WEBHOOK_ENV_NAME)
        if env_file is not None
        else ""
    )
    if not url:
        url = os.environ.get(WEBHOOK_ENV_NAME, "").strip()
    if not url:
        raise HotelierNotificationError(
            f"{WEBHOOK_ENV_NAME} is not configured in the selected env file or process environment"
        )
    return validate_webhook_url(url)


def _retry_delay(error: urllib.error.HTTPError, attempt: int) -> float:
    retry_after = error.headers.get("Retry-After") if error.headers else None
    try:
        return min(10.0, max(0.25, float(retry_after or "")))
    except ValueError:
        return 0.5 * (2**attempt)


def post_discord_message(
    webhook_url: str,
    message: str,
    *,
    timeout: int = 15,
) -> None:
    """Post one bounded message without logging or persisting the destination."""

    if len(message) > DISCORD_MESSAGE_LIMIT:
        raise HotelierNotificationError(
            "Hotelier Discord message exceeds the 2,000-character limit"
        )
    payload = json.dumps(
        {"content": message, "allowed_mentions": {"parse": []}},
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode("utf-8")
    request = urllib.request.Request(
        webhook_url,
        data=payload,
        headers={
            "Content-Type": "application/json",
            "User-Agent": "auto-albamon-hotelier-discord/1.0",
        },
        method="POST",
    )
    for attempt in range(3):
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                response.read()
                if response.status not in {200, 204}:
                    raise HotelierNotificationError(
                        f"Hotelier Discord webhook returned HTTP {response.status}"
                    )
            return
        except urllib.error.HTTPError as exc:
            retryable = exc.code == 429 or 500 <= exc.code < 600
            if not retryable or attempt == 2:
                raise HotelierNotificationError(
                    f"Hotelier Discord webhook returned HTTP {exc.code}"
                ) from exc
            time.sleep(_retry_delay(exc, attempt))
        except (urllib.error.URLError, TimeoutError) as exc:
            # Delivery after a transport timeout is ambiguous, so an immediate
            # retry could duplicate an accepted message.
            raise HotelierNotificationError(
                "Hotelier Discord webhook delivery failed at the network transport"
            ) from exc


def _validate_report(report: dict[str, Any]) -> list[dict[str, Any]]:
    if report.get("integrity_checks_passed") is not True:
        raise HotelierNotificationError(
            "Hotelier report integrity checks did not pass; refusing Discord delivery"
        )
    jobs = report.get("jobs")
    if not isinstance(jobs, list):
        raise HotelierNotificationError("Hotelier report jobs must be a list")
    ids = [str(job.get("group_id") or "") for job in jobs if isinstance(job, dict)]
    if len(ids) != len(jobs) or any(not value for value in ids) or len(ids) != len(set(ids)):
        raise HotelierNotificationError(
            "Hotelier report has missing or duplicate group IDs"
        )
    return jobs


def _read_state(path: Path) -> tuple[dict[str, Any] | None, int | None]:
    if not path.exists():
        return None, None
    try:
        state = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise HotelierNotificationError(
            "Hotelier Discord state is unreadable or invalid"
        ) from exc
    if state.get("version") == 1 and isinstance(state.get("jobs"), dict):
        # Version 1 tracked every underlying shift.  The hotel-level projection
        # is materially different, so one complete aggregated digest is the
        # safest and most useful migration baseline.
        return None, 1
    if state.get("version") != STATE_VERSION or not isinstance(
        state.get("hotels"), dict
    ):
        raise HotelierNotificationError(
            "Hotelier Discord state has an incompatible shape"
        )
    return state, None


def _write_state(path: Path, state: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_text(
        path,
        json.dumps(state, ensure_ascii=False, separators=(",", ":")) + "\n",
    )


def _state_entry(
    hotel: dict[str, Any],
    *,
    observed_at: datetime,
    notified: bool,
) -> dict[str, Any]:
    timestamp = observed_at.isoformat(timespec="seconds")
    return {
        "fingerprint": hotel_fingerprint(hotel),
        "first_seen_at": timestamp,
        "last_seen_at": timestamp,
        "notified_at": timestamp if notified else "",
    }


def _prune_state(state: dict[str, Any], *, observed_at: datetime) -> None:
    cutoff = observed_at - timedelta(days=STATE_RETENTION_DAYS)
    oldest = datetime.min.replace(tzinfo=observed_at.tzinfo)
    for hotel_id, entry in list(state["hotels"].items()):
        try:
            last_seen = datetime.fromisoformat(str(entry.get("last_seen_at") or ""))
            if last_seen.tzinfo is None:
                last_seen = last_seen.astimezone()
        except (AttributeError, TypeError, ValueError):
            last_seen = oldest
        if last_seen < cutoff:
            del state["hotels"][hotel_id]


def _notify_report(
    report: dict[str, Any],
    *,
    state_path: Path,
    sender: Callable[[str], None],
    dry_run: bool,
    observed_at: datetime,
) -> dict[str, int | bool]:
    jobs = _validate_report(report)
    hotels = aggregate_hotels(jobs)
    current_by_id = {str(hotel["hotel_id"]): hotel for hotel in hotels}
    state, migrated_from_version = _read_state(state_path)
    timestamp = observed_at.isoformat(timespec="seconds")

    if state is None:
        pages = format_complete_digest(report, hotels)
        if dry_run:
            for page in pages:
                sender(page.message)
            return {
                "bootstrapped": True,
                "actionable_jobs": len(jobs),
                "aggregated_hotels": len(hotels),
                "new_hotels": 0,
                "changed_hotels": 0,
                "announced_hotels": len(hotels),
                "sent_messages": 0,
                "preview_messages": len(pages),
            }
        state = {
            "version": STATE_VERSION,
            "updated_at": timestamp,
            "retention_days": STATE_RETENTION_DAYS,
            "hotels": {},
            "full_digest": {
                "fingerprint": digest_fingerprint(hotels),
                "format_version": DIGEST_FORMAT_VERSION,
                "sent_pages": 0,
                "total_pages": len(pages),
                "complete": False,
                "migrated_from_version": migrated_from_version,
            },
        }
        _write_state(state_path, state)

    digest_state = state.get("full_digest") or {}
    if not digest_state.get("complete"):
        pages = format_complete_digest(report, hotels)
        current_fingerprint = digest_fingerprint(hotels)
        if (
            digest_state.get("fingerprint") != current_fingerprint
            or digest_state.get("format_version") != DIGEST_FORMAT_VERSION
        ):
            digest_state.update(
                {
                    "fingerprint": current_fingerprint,
                    "format_version": DIGEST_FORMAT_VERSION,
                    "sent_pages": 0,
                    "total_pages": len(pages),
                    "complete": False,
                }
            )
            state["full_digest"] = digest_state
            _write_state(state_path, state)
        start_page = min(int(digest_state.get("sent_pages") or 0), len(pages))
        for page_index in range(start_page, len(pages)):
            sender(pages[page_index].message)
            digest_state["sent_pages"] = page_index + 1
            state["updated_at"] = timestamp
            _write_state(state_path, state)
        state["hotels"] = {
            hotel_id: _state_entry(
                hotel, observed_at=observed_at, notified=True
            )
            for hotel_id, hotel in current_by_id.items()
        }
        digest_state["complete"] = True
        digest_state["completed_at"] = timestamp
        state["updated_at"] = timestamp
        _write_state(state_path, state)
        return {
            "bootstrapped": True,
            "actionable_jobs": len(jobs),
            "aggregated_hotels": len(hotels),
            "new_hotels": 0,
            "changed_hotels": 0,
            "announced_hotels": len(hotels),
            "sent_messages": len(pages) - start_page,
            "resumed_from_page": start_page,
        }

    known = state["hotels"]
    candidates: list[tuple[str, dict[str, Any]]] = []
    for hotel in hotels:
        hotel_id = str(hotel["hotel_id"])
        prior = known.get(hotel_id)
        if not isinstance(prior, dict):
            candidates.append(("new", hotel))
        elif prior.get("fingerprint") != hotel_fingerprint(hotel):
            candidates.append(("changed", hotel))

    pages = format_update_digest(candidates, hotels) if candidates else []
    for page in pages:
        sender(page.message)
        if not dry_run:
            for hotel_id in page.hotel_ids:
                known[hotel_id] = _state_entry(
                    current_by_id[hotel_id],
                    observed_at=observed_at,
                    notified=True,
                )
            state["updated_at"] = timestamp
            _write_state(state_path, state)

    if not dry_run:
        for hotel_id in current_by_id:
            entry = known.get(hotel_id)
            if isinstance(entry, dict):
                entry["last_seen_at"] = timestamp
        _prune_state(state, observed_at=observed_at)
        state["updated_at"] = timestamp
        _write_state(state_path, state)
    return {
        "bootstrapped": False,
        "actionable_jobs": len(jobs),
        "aggregated_hotels": len(hotels),
        "new_hotels": sum(kind == "new" for kind, _ in candidates),
        "changed_hotels": sum(kind == "changed" for kind, _ in candidates),
        "announced_hotels": len(candidates),
        "sent_messages": 0 if dry_run else len(pages),
        "preview_messages": len(pages) if dry_run else 0,
    }


def notify_report(
    report: dict[str, Any],
    *,
    state_path: Path = DEFAULT_STATE_PATH,
    sender: Callable[[str], None],
    dry_run: bool = False,
    observed_at: datetime | None = None,
) -> dict[str, int | bool]:
    """Serialize state mutations so overlapping runs cannot double-send."""

    observed_at = observed_at or _now()
    if dry_run:
        return _notify_report(
            report,
            state_path=state_path,
            sender=sender,
            dry_run=True,
            observed_at=observed_at,
        )
    lock_path = state_path.with_suffix(state_path.suffix + ".lock")
    with exclusive_file_lock(lock_path):
        return _notify_report(
            report,
            state_path=state_path,
            sender=sender,
            dry_run=False,
            observed_at=observed_at,
        )
