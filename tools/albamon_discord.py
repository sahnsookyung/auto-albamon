#!/usr/bin/env python3
"""Send newly actionable Albamon jobs to a Discord webhook.

The notifier consumes the already-screened combined and unified reports.  It
does not make eligibility decisions itself, and it never stores the webhook
URL.  A semantic job identity prevents a routine Albamon repost from being
announced as a new opportunity.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Callable

try:
    from tools.albamon_combined_jobs import normalized_text, semantic_duplicate_key
    from tools.albamon_runtime import (
        AlreadyRunningError,
        atomic_write_text,
        exclusive_file_lock,
    )
    from tools.albamon_transit import ORIGIN as TRANSIT_ORIGIN
    from tools.jobscout_config import SETTINGS
except ModuleNotFoundError:  # Support direct execution from the repository root.
    from albamon_combined_jobs import normalized_text, semantic_duplicate_key
    from albamon_runtime import (
        AlreadyRunningError,
        atomic_write_text,
        exclusive_file_lock,
    )
    from albamon_transit import ORIGIN as TRANSIT_ORIGIN
    from jobscout_config import SETTINGS


DEFAULT_COMBINED_REPORT = Path("artifacts/albamon/latest-combined.json")
DEFAULT_UNIFIED_REPORT = Path("artifacts/albamon/latest-unified-report.json")
DEFAULT_STATE_PATH = Path("artifacts/albamon/cache/discord_notifications.json")
DEFAULT_WEBHOOK_ENV_FILE = Path(".env")
STATE_VERSION = 1
STATE_RETENTION_DAYS = 90
DISCORD_MESSAGE_LIMIT = 2_000
DISCORD_WEBHOOK_HOSTS = {
    "discord.com",
    "canary.discord.com",
    "ptb.discord.com",
    "discordapp.com",
}


class DiscordNotificationError(RuntimeError):
    """Raised when a Discord notification cannot be delivered safely."""


def _now() -> datetime:
    return datetime.now().astimezone()


def load_json_object(path: Path, *, label: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise ValueError(f"{label} not found: {path}") from exc
    except json.JSONDecodeError as exc:
        raise ValueError(f"{label} contains invalid JSON: {path}") from exc
    if not isinstance(value, dict):
        raise ValueError(f"{label} must contain a JSON object: {path}")
    return value


def _env_file_value(path: Path, name: str) -> str:
    """Read one exact environment variable without evaluating the env file."""

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


def validate_discord_webhook_url(url: str) -> str:
    """Accept only a normal HTTPS Discord webhook URL."""

    parsed = urllib.parse.urlparse(url)
    host = (parsed.hostname or "").lower()
    if parsed.scheme != "https" or host not in DISCORD_WEBHOOK_HOSTS:
        raise ValueError("Discord webhook must use HTTPS on an official Discord host")
    if parsed.username or parsed.password:
        raise ValueError("Discord webhook URL must not contain URL credentials")
    if not re.fullmatch(r"/api(?:/v\d+)?/webhooks/\d+/[^/]+/?", parsed.path):
        raise ValueError("Discord webhook URL has an unexpected path")
    return url


def resolve_discord_webhook(env_file: Path | None) -> str:
    # An explicitly selected env file is the delivery destination contract.
    # This prevents a stale inherited variable from redirecting a scheduled run.
    url = (
        _env_file_value(env_file, "DISCORD_WEBHOOK_URL")
        if env_file is not None
        else ""
    )
    if not url:
        url = os.environ.get("DISCORD_WEBHOOK_URL", "").strip()
    if not url:
        raise ValueError(
            "DISCORD_WEBHOOK_URL is not configured in the environment or webhook env file"
        )
    return validate_discord_webhook_url(url)


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
    """Post one bounded message without exposing the webhook in diagnostics."""

    if len(message) > DISCORD_MESSAGE_LIMIT:
        raise ValueError("Discord message exceeds the 2,000-character limit")
    payload = json.dumps(
        {
            "content": message,
            "allowed_mentions": {"parse": []},
        },
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode("utf-8")
    request = urllib.request.Request(
        webhook_url,
        data=payload,
        headers={
            "Content-Type": "application/json",
            "User-Agent": "auto-albamon-discord/1.0",
        },
        method="POST",
    )
    for attempt in range(3):
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                response.read()
                if response.status not in {200, 204}:
                    raise DiscordNotificationError(
                        f"Discord webhook returned HTTP {response.status}"
                    )
            return
        except urllib.error.HTTPError as exc:
            retryable = exc.code == 429 or 500 <= exc.code < 600
            if not retryable or attempt == 2:
                raise DiscordNotificationError(
                    f"Discord webhook returned HTTP {exc.code}"
                ) from exc
            time.sleep(_retry_delay(exc, attempt))
        except (urllib.error.URLError, TimeoutError) as exc:
            # A transport timeout has an ambiguous delivery outcome.  Avoid an
            # immediate retry that could duplicate a successfully accepted post.
            raise DiscordNotificationError(
                f"Discord webhook delivery failed: {exc.reason if hasattr(exc, 'reason') else exc}"
            ) from exc


def _fallback_semantic_key(record: dict[str, Any]) -> str:
    return "|".join(
        normalized_text(record.get(field))
        for field in ("title", "company", "area", "work_time")
    )


def notification_identity(record: dict[str, Any]) -> str:
    """Return a privacy-minimized stable identity for notification state."""

    # A location-marketing repost stays stable, while a materially different
    # shift or guaranteed-pay offer is independently actionable and notifyable.
    identity = semantic_duplicate_key(record, include_material_terms=True)
    if not identity.strip("|"):
        identity = _fallback_semantic_key(record)
    return hashlib.sha256(identity.encode("utf-8")).hexdigest()


def _combined_records(report: dict[str, Any]) -> list[dict[str, Any]]:
    return [*(report.get("jobs") or []), *(report.get("logistics_jobs") or [])]


def validate_report_pair(
    combined_report: dict[str, Any], unified_report: dict[str, Any]
) -> None:
    """Refuse mixed, duplicate, or partial report generations."""

    combined_records = _combined_records(combined_report)
    unified_records = unified_report.get("jobs") or []
    if not isinstance(unified_records, list):
        raise ValueError("Unified report jobs must be a list")
    try:
        combined_ids = [int(record["recruit_no"]) for record in combined_records]
        unified_ids = [int(record["recruit_no"]) for record in unified_records]
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError("Reports contain an invalid recruit number") from exc
    if len(combined_ids) != len(set(combined_ids)):
        raise ValueError("Combined report contains duplicate recruit numbers")
    if len(unified_ids) != len(set(unified_ids)):
        raise ValueError("Unified report contains duplicate recruit numbers")
    if set(combined_ids) != set(unified_ids):
        raise ValueError(
            "Combined and unified reports contain different job generations"
        )
    combined_run_id = str(combined_report.get("run_id") or "")
    unified_run_id = str(unified_report.get("source_run_id") or "")
    if combined_run_id or unified_run_id:
        if not combined_run_id or combined_run_id != unified_run_id:
            raise ValueError("Combined and unified report run IDs do not match")
    source_generated_at = str(unified_report.get("source_generated_at") or "")
    combined_generated_at = str(combined_report.get("generated_at") or "")
    if source_generated_at and source_generated_at != combined_generated_at:
        raise ValueError("Unified report references a different combined timestamp")


def actionable_jobs(
    combined_report: dict[str, Any], unified_report: dict[str, Any]
) -> list[tuple[str, dict[str, Any]]]:
    """Join actionable unified records to the richer combined job identity."""

    combined_by_id = {
        int(record["recruit_no"]): record
        for record in _combined_records(combined_report)
        if isinstance(record, dict) and record.get("recruit_no") is not None
    }
    result: list[tuple[str, dict[str, Any]]] = []
    for record in unified_report.get("jobs") or []:
        if not isinstance(record, dict) or record.get("application_disposition") != "apply":
            continue
        recruit_no = int(record["recruit_no"])
        identity_record = combined_by_id[recruit_no]
        result.append((notification_identity(identity_record), record))
    result.sort(key=lambda item: int(item[1].get("utility_rank") or 999_999))
    return result


def _safe_discord_text(value: Any, *, limit: int) -> str:
    text = " ".join(str(value or "").split())
    text = text.replace("@", "@\u200b").replace("```", "''' ")
    if len(text) > limit:
        return text[: limit - 1].rstrip() + "…"
    return text


def _list_text(values: Any, *, empty: str, limit: int) -> str:
    if not isinstance(values, list):
        return empty
    rendered = "; ".join(str(value) for value in values if value)
    return _safe_discord_text(rendered or empty, limit=limit)


def _late_night_commute_text(record: dict[str, Any]) -> str:
    def short_time(value: Any) -> str:
        text = str(value or "")
        return text[5:16].replace("T", " ") if len(text) >= 16 else text

    rendered: list[str] = []
    for journey in record.get("late_night_shift_journeys") or []:
        outbound = journey.get("outbound") or {}
        return_route = journey.get("return") or {}
        bicycle = journey.get("bicycle_fallback") or {}
        if return_route.get("mode") == "public_transit_future_time":
            vehicles = ", ".join(return_route.get("vehicles") or ["대중교통"])
            home = (
                f"Naver {vehicles} {return_route.get('minutes')}분 "
                f"({short_time(return_route.get('departure_at'))}→"
                f"{short_time(return_route.get('arrival_at'))})"
            )
        elif return_route.get("mode") == "night_bus_pattern":
            vehicles = ", ".join(return_route.get("vehicles") or ["심야버스"])
            home = f"{vehicles} {return_route.get('minutes')}분"
        elif return_route.get("mode") == "public_transit_pattern":
            home = f"대중교통 경로 {return_route.get('minutes')}분"
        else:
            home = f"자전거 {return_route.get('minutes')}분"
        outbound_text = f"출근 {outbound.get('minutes')}분"
        if outbound.get("mode") == "public_transit_future_time":
            outbound_text += (
                f" Naver({short_time(outbound.get('departure_at'))}→"
                f"{short_time(outbound.get('arrival_at'))})"
            )
        rendered.append(
            f"{journey.get('shift')}: {outbound_text} · "
            f"귀가 {home} · 자전거 예비 {bicycle.get('minutes')}분/"
            f"{bicycle.get('distance_km')}km"
        )
    return _safe_discord_text("; ".join(rendered), limit=360)


def format_job_message(record: dict[str, Any], *, position: int, total: int) -> str:
    """Render the application-ready details for one new Discord notification."""

    company = _safe_discord_text(record.get("company"), limit=100)
    title = _safe_discord_text(record.get("title"), limit=180)
    salary = _safe_discord_text(
        f"{record.get('salary_type', '')} {record.get('salary', '')}", limit=80
    )
    hourly = int(record.get("comparable_hourly_pay") or 0)
    hourly_text = (
        f"근무 환산시급 약 {hourly:,}원"
        if record.get("pay_comparison_reliable") is not False and hourly > 0
        else "근무 환산시급 확인 필요"
    )
    commute = record.get("transit_minutes")
    transfers = record.get("transit_transfers")
    remote_job = bool(
        record.get("remote_job")
        or "remote_short_term" in (record.get("eligibility_lanes") or [])
    )
    commute_text = "재택근무 · 통근 없음" if remote_job else (
        f"{TRANSIT_ORIGIN['name']} {commute}분"
        + (f", 환승 {transfers}회" if isinstance(transfers, int) else "")
        + (", 오래된 경로—출발 전 재확인" if record.get("transit_route_stale") else "")
        if isinstance(commute, int)
        else "실제 근무지 통근 확인 필요"
    )
    verify = _list_text(
        record.get("verify_before_sending"), empty="추가 확인사항 없음", limit=320
    )
    requirements = _list_text(
        record.get("hard_requirements"), empty="명시된 하드 요건 없음", limit=240
    )
    application_methods = _list_text(
        record.get("application_methods"), empty="지원방법 확인 필요", limit=100
    )
    message = _safe_discord_text(record.get("message"), limit=520)
    duration = _safe_discord_text(
        record.get("duration_requirement") or "숫자 최소기간 미확인", limit=180
    )
    availability_end = _safe_discord_text(
        record.get("availability_end_date") or "", limit=20
    )
    area = _safe_discord_text(record.get("area"), limit=180)
    schedule = _safe_discord_text(
        "; ".join(
            part
            for part in (
                str(record.get("work_days") or ""),
                str(record.get("work_time") or ""),
                ", ".join(record.get("weekend_shift_options") or []),
                ", ".join(record.get("target_date_options") or []),
                ", ".join(record.get("late_night_shift_options") or []),
            )
            if part
        ),
        limit=260,
    )
    late_night = "songpa_late_night" in (record.get("eligibility_lanes") or [])
    temporary_single_day = bool(
        {"temporary_single_day", "aug_15_17_single_day"}
        & set(record.get("eligibility_lanes") or [])
    )
    late_night_commute = _late_night_commute_text(record) if late_night else ""
    station_area_text = ""
    if late_night and record.get("evening_nearest_station"):
        station_area_text = (
            f"{record['evening_nearest_station']} 도보권 약 "
            f"{record.get('evening_station_walk_minutes')}분 "
            f"(직선 {record.get('evening_station_distance_km')}km, 보수적 추정)"
        )
    url = str(record.get("url") or "")
    evening_label = (
        f"{'·'.join(SETTINGS.albamon.evening.stations)} 저녁 · "
        f"{SETTINGS.albamon.evening.available_from}~"
        f"{SETTINGS.albamon.evening.available_until} · 역세권 도보 추정 "
        f"{SETTINGS.albamon.evening.station_walk_limit_minutes}분 이하"
    )
    target_dates = ", ".join(record.get("target_date_options") or [])
    rendered = "\n".join(
        [
            f"🆕 **새 알바몬 지원 후보 {position}/{total}**",
            *(["🏠 **재택근무 · 통근 없음**"] if remote_job else []),
            *([f"🌙 **{evening_label}**"] if late_night else []),
            *(
                [f"📅 **{target_dates or '표시된 날짜'} 중 1일만 선택 가능**"]
                if temporary_single_day
                else []
            ),
            f"**{company} — {title}**",
            url,
            f"💰 {salary} · {hourly_text}",
            f"📍 {area}",
            f"🗓️ {schedule}",
            f"🚇 {commute_text}",
            *([f"📌 {station_area_text}"] if station_area_text else []),
            *([f"🌙 {late_night_commute}"] if late_night_commute else []),
            f"⏳ {duration}",
            *(
                [f"📆 필수 근무 종료: {availability_end} 이내"]
                if availability_end
                else []
            ),
            f"✅ 하드 요건: {requirements}",
            f"📨 지원방법: {application_methods}",
            "",
            "**알바몬 지원 메시지**",
            f"> {message}",
            "",
            f"**보내기 전 확인:** {verify}",
        ]
    )
    if len(rendered) <= DISCORD_MESSAGE_LIMIT:
        return rendered
    # Exceptionally verbose jobs retain their link and decision-critical fields;
    # the tailored copy can still be read in the local unified report.
    compact = "\n".join(rendered.splitlines()[:11])
    suffix = "\n\n지원 메시지와 전체 확인사항은 latest-unified-report.md에서 확인하세요."
    return compact[: DISCORD_MESSAGE_LIMIT - len(suffix)].rstrip() + suffix


def load_state(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    state = load_json_object(path, label="Discord notification state")
    if state.get("version") != STATE_VERSION or not isinstance(state.get("jobs"), dict):
        raise ValueError(f"Discord notification state has an incompatible shape: {path}")
    return state


def _state_entry(
    record: dict[str, Any], *, observed_at: datetime, notified: bool = True
) -> dict[str, Any]:
    return {
        "recruit_no": int(record["recruit_no"]),
        "first_seen_at": observed_at.isoformat(timespec="seconds"),
        "last_seen_at": observed_at.isoformat(timespec="seconds"),
        "notified_at": (
            observed_at.isoformat(timespec="seconds") if notified else ""
        ),
    }


def write_state(path: Path, state: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_text(
        path,
        json.dumps(state, ensure_ascii=False, separators=(",", ":")) + "\n",
    )


def _parse_state_datetime(value: Any) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(str(value))
    except (TypeError, ValueError):
        return None
    return parsed if parsed.tzinfo is not None else parsed.astimezone()


def _prune_state_jobs(state: dict[str, Any], *, observed_at: datetime) -> None:
    cutoff = observed_at - timedelta(days=STATE_RETENTION_DAYS)
    oldest = datetime.min.replace(tzinfo=observed_at.tzinfo)
    jobs = state["jobs"]
    stale = [
        key
        for key, entry in jobs.items()
        if not isinstance(entry, dict)
        or (_parse_state_datetime(entry.get("last_seen_at")) or oldest) < cutoff
    ]
    for key in stale:
        del jobs[key]


def _notify_new_jobs(
    combined_report: dict[str, Any],
    unified_report: dict[str, Any],
    *,
    state_path: Path,
    sender: Callable[[str], None],
    bootstrap: bool = False,
    dry_run: bool = False,
) -> dict[str, int | bool]:
    """Send new actionable jobs and atomically remember successful deliveries."""

    validate_report_pair(combined_report, unified_report)
    observed_at = _now()
    current_jobs = actionable_jobs(combined_report, unified_report)
    state = load_state(state_path)
    if state is None:
        state = {
            "version": STATE_VERSION,
            "updated_at": observed_at.isoformat(timespec="seconds"),
            "retention_days": STATE_RETENTION_DAYS,
            "jobs": {},
        }
        bootstrap = True
    jobs_state = state["jobs"]

    if bootstrap:
        for identity, record in current_jobs:
            jobs_state.setdefault(
                identity,
                _state_entry(record, observed_at=observed_at, notified=False),
            )
        state["updated_at"] = observed_at.isoformat(timespec="seconds")
        if not dry_run:
            write_state(state_path, state)
        return {
            "bootstrapped": True,
            "actionable_jobs": len(current_jobs),
            "new_jobs": 0,
            "sent_jobs": 0,
        }

    known_by_recruit_no = {
        int(entry["recruit_no"]): (identity, entry)
        for identity, entry in jobs_state.items()
        if isinstance(entry, dict) and entry.get("recruit_no") is not None
    }
    new_jobs: list[tuple[str, dict[str, Any]]] = []
    for identity, record in current_jobs:
        if identity in jobs_state:
            continue
        prior = known_by_recruit_no.get(int(record["recruit_no"]))
        if prior is None:
            new_jobs.append((identity, record))
            continue
        # Detail edits can change the semantic fingerprint without creating a
        # new recruit. Migrate that state instead of announcing the same ad.
        prior_identity, prior_entry = prior
        jobs_state[identity] = prior_entry
        if prior_identity != identity:
            del jobs_state[prior_identity]
    if dry_run:
        for position, (_, record) in enumerate(new_jobs, start=1):
            sender(format_job_message(record, position=position, total=len(new_jobs)))
        return {
            "bootstrapped": False,
            "actionable_jobs": len(current_jobs),
            "new_jobs": len(new_jobs),
            "sent_jobs": 0,
            "preview_messages": len(new_jobs),
        }

    for position, (identity, record) in enumerate(new_jobs, start=1):
        sender(format_job_message(record, position=position, total=len(new_jobs)))
        jobs_state[identity] = _state_entry(record, observed_at=observed_at)
        state["updated_at"] = observed_at.isoformat(timespec="seconds")
        write_state(state_path, state)

    current_by_identity = dict(current_jobs)
    for identity, entry in jobs_state.items():
        if identity not in current_by_identity or not isinstance(entry, dict):
            continue
        entry["last_seen_at"] = observed_at.isoformat(timespec="seconds")
        entry["recruit_no"] = int(current_by_identity[identity]["recruit_no"])
    _prune_state_jobs(state, observed_at=observed_at)
    state["updated_at"] = observed_at.isoformat(timespec="seconds")
    write_state(state_path, state)
    return {
        "bootstrapped": False,
        "actionable_jobs": len(current_jobs),
        "new_jobs": len(new_jobs),
        "sent_jobs": len(new_jobs),
        "sent_messages": len(new_jobs),
    }


def notify_new_jobs(
    combined_report: dict[str, Any],
    unified_report: dict[str, Any],
    *,
    state_path: Path,
    sender: Callable[[str], None],
    bootstrap: bool = False,
    dry_run: bool = False,
) -> dict[str, int | bool]:
    """Serialize state changes so overlapping runs cannot double-send jobs."""

    if dry_run:
        return _notify_new_jobs(
            combined_report,
            unified_report,
            state_path=state_path,
            sender=sender,
            bootstrap=bootstrap,
            dry_run=True,
        )
    lock_path = state_path.with_suffix(state_path.suffix + ".lock")
    with exclusive_file_lock(lock_path):
        return _notify_new_jobs(
            combined_report,
            unified_report,
            state_path=state_path,
            sender=sender,
            bootstrap=bootstrap,
            dry_run=False,
        )


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--combined", type=Path, default=DEFAULT_COMBINED_REPORT)
    parser.add_argument("--report", type=Path, default=DEFAULT_UNIFIED_REPORT)
    parser.add_argument("--state", type=Path, default=DEFAULT_STATE_PATH)
    parser.add_argument(
        "--webhook-env-file", type=Path, default=DEFAULT_WEBHOOK_ENV_FILE
    )
    parser.add_argument(
        "--bootstrap",
        action="store_true",
        help="Record current actionable jobs without sending them.",
    )
    parser.add_argument(
        "--announce-bootstrap",
        action="store_true",
        help="Send one confirmation after establishing a baseline.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print new-job messages without sending or changing state.",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        combined_report = load_json_object(args.combined, label="Combined report")
        unified_report = load_json_object(args.report, label="Unified report")
        webhook_url = "" if args.dry_run else resolve_discord_webhook(args.webhook_env_file)

        def sender(message: str) -> None:
            if args.dry_run:
                print(message)
                print("\n---\n")
            else:
                post_discord_message(webhook_url, message)

        metrics = notify_new_jobs(
            combined_report,
            unified_report,
            state_path=args.state,
            sender=sender,
            bootstrap=args.bootstrap,
            dry_run=args.dry_run,
        )
        if args.announce_bootstrap:
            if args.dry_run:
                raise ValueError("--announce-bootstrap cannot be combined with --dry-run")
            if not metrics["bootstrapped"]:
                raise ValueError("--announce-bootstrap requires a bootstrap run")
            post_discord_message(
                webhook_url,
                "✅ **알바몬 4시간 모니터링을 시작했습니다.**\n"
                f"현재 지원 후보 {metrics['actionable_jobs']}건은 기준선으로 기록했고, "
                "다음 실행부터 새 지원 후보만 상세 내용과 지원 메시지로 알려드립니다.",
            )
    except AlreadyRunningError:
        print(
            json.dumps(
                {"skipped": "notification_already_running"},
                ensure_ascii=False,
                separators=(",", ":"),
            )
        )
        return 0
    except (DiscordNotificationError, OSError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(metrics, ensure_ascii=False, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
