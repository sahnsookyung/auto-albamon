"""Bounded, privacy-minimized incremental cache for Albamon job details."""

from __future__ import annotations

import hashlib
import json
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any


CACHE_VERSION = 1
DEFAULT_DETAIL_CACHE_PATH = Path("artifacts/albamon/cache/details.json")
DEFAULT_EXCLUDED_TTL_DAYS = 7
DEFAULT_RETENTION_DAYS = 30

FINGERPRINT_FIELDS = (
    "recruitNo",
    "recruitTitle",
    "companyName",
    "payType",
    "pay",
    "workingTime",
    "workingPeriod",
    "workingWeek",
    "workingDate",
    "closingDate",
    "closingDateWithDDay",
    "postStatusType",
    "recruitStatus",
    "workplaceAddress",
    "workplaceArea",
    "latitude",
    "longitude",
    "parts",
    "applicationTypes",
    "isCoupang",
)

# The public detail response contains fields that the report does not use and
# that are inappropriate to retain in a long-lived cache.
PRIVATE_DETAIL_FIELDS = {
    "managerEmail",
    "managerFax",
    "nonMembersPassword",
    "phoneNumber",
    "recruiterKey",
    "safeNumber",
    "shareKey",
    "userIpAddress",
}


def _now() -> datetime:
    return datetime.now().astimezone()


def _parse_datetime(value: Any) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(str(value))
    except (TypeError, ValueError):
        return None
    return parsed if parsed.tzinfo is not None else parsed.astimezone()


def search_job_fingerprint(job: dict[str, Any]) -> str:
    """Return a stable fingerprint for list fields that signal a changed ad."""

    payload = {field: job.get(field) for field in FINGERPRINT_FIELDS}
    serialized = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


def sanitize_detail_for_cache(detail: dict[str, Any]) -> dict[str, Any]:
    """Drop unused private contact/authentication fields before persistence."""

    return {
        key: value
        for key, value in detail.items()
        if key not in PRIVATE_DETAIL_FIELDS
    }


@dataclass
class DetailCache:
    path: Path
    excluded_ttl_days: int = DEFAULT_EXCLUDED_TTL_DAYS
    retention_days: int = DEFAULT_RETENTION_DAYS
    checkpoint_every: int = 100
    entries: dict[str, dict[str, Any]] = field(default_factory=dict)
    stats: Counter[str] = field(default_factory=Counter)
    _updates_since_checkpoint: int = 0

    @classmethod
    def load(
        cls,
        path: Path,
        *,
        excluded_ttl_days: int = DEFAULT_EXCLUDED_TTL_DAYS,
        retention_days: int = DEFAULT_RETENTION_DAYS,
        checkpoint_every: int = 100,
    ) -> "DetailCache":
        cache = cls(
            path=path,
            excluded_ttl_days=excluded_ttl_days,
            retention_days=retention_days,
            checkpoint_every=checkpoint_every,
        )
        if not path.exists():
            return cache
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            cache.stats["invalid_cache_files"] += 1
            return cache
        if payload.get("version") != CACHE_VERSION or not isinstance(
            payload.get("entries"), dict
        ):
            cache.stats["incompatible_cache_files"] += 1
            return cache
        cache.entries = payload["entries"]
        return cache

    def lookup(
        self,
        job: dict[str, Any],
        *,
        force_refresh: bool = False,
        now: datetime | None = None,
    ) -> dict[str, Any] | None:
        recruit_no = str(int(job["recruitNo"]))
        entry = self.entries.get(recruit_no)
        observed_at = now or _now()
        if entry is not None:
            entry["last_seen_at"] = observed_at.isoformat(timespec="seconds")
        if force_refresh:
            self.stats["forced_refreshes"] += 1
            return None
        if not isinstance(entry, dict):
            self.stats["new_jobs"] += 1
            return None
        if entry.get("fingerprint") != search_job_fingerprint(job):
            self.stats["changed_jobs"] += 1
            return None
        detail = entry.get("detail")
        if not isinstance(detail, dict):
            self.stats["missing_details"] += 1
            return None
        fetched_at = _parse_datetime(entry.get("fetched_at"))
        if fetched_at is None:
            self.stats["expired_entries"] += 1
            return None
        if observed_at - fetched_at > timedelta(days=self.excluded_ttl_days):
            self.stats["expired_entries"] += 1
            return None
        self.stats["cache_hits"] += 1
        return detail

    def store(
        self,
        job: dict[str, Any],
        detail: dict[str, Any],
        *,
        fetched_via: str,
        now: datetime | None = None,
    ) -> None:
        observed_at = now or _now()
        recruit_no = str(int(job["recruitNo"]))
        self.entries[recruit_no] = {
            "fingerprint": search_job_fingerprint(job),
            "fetched_at": observed_at.isoformat(timespec="seconds"),
            "last_seen_at": observed_at.isoformat(timespec="seconds"),
            "fetched_via": fetched_via,
            "classification": self.entries.get(recruit_no, {}).get(
                "classification", "unknown"
            ),
            "detail": sanitize_detail_for_cache(detail),
        }
        self.stats["cache_writes"] += 1
        self._updates_since_checkpoint += 1
        if (
            self.checkpoint_every > 0
            and self._updates_since_checkpoint >= self.checkpoint_every
        ):
            self.save()
            self.stats["checkpoints"] += 1

    def mark_classification(self, recruit_no: int, classification: str) -> None:
        entry = self.entries.get(str(recruit_no))
        if isinstance(entry, dict):
            entry["classification"] = classification

    def requires_live_refresh(self, recruit_no: int) -> bool:
        entry = self.entries.get(str(recruit_no))
        return isinstance(entry, dict) and entry.get("classification") in {
            "eligible",
            "applied",
        }

    def prune(self, current_recruit_nos: set[int]) -> int:
        """Bound cache growth while retaining recent entries for interrupted runs."""

        cutoff = _now() - timedelta(days=self.retention_days)
        stale_ids = []
        for recruit_no, entry in self.entries.items():
            if int(recruit_no) in current_recruit_nos:
                continue
            last_seen = _parse_datetime(entry.get("last_seen_at"))
            if last_seen is None or last_seen < cutoff:
                stale_ids.append(recruit_no)
        for recruit_no in stale_ids:
            del self.entries[recruit_no]
        self.stats["pruned_entries"] += len(stale_ids)
        return len(stale_ids)

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "version": CACHE_VERSION,
            "updated_at": _now().isoformat(timespec="seconds"),
            "excluded_ttl_days": self.excluded_ttl_days,
            "retention_days": self.retention_days,
            "entry_count": len(self.entries),
            "privacy_note": (
                "Unused contact and authentication fields are removed before caching."
            ),
            "entries": self.entries,
        }
        temporary_path = self.path.with_suffix(self.path.suffix + ".tmp")
        temporary_path.write_text(
            json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + "\n",
            encoding="utf-8",
        )
        temporary_path.replace(self.path)
        self._updates_since_checkpoint = 0

    def metrics(self) -> dict[str, int]:
        return dict(sorted(self.stats.items()))
