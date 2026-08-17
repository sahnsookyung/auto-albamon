"""Atomic JSON, CSV, and Markdown rendering for the separate report."""

from __future__ import annotations

import csv
import io
import json
from pathlib import Path
from typing import Any

try:
    from tools.albamon_runtime import atomic_text_writer, atomic_write_text
except ModuleNotFoundError:  # Direct CLI execution via tools/.
    from albamon_runtime import atomic_text_writer, atomic_write_text

from .config import SOURCE_LABELS


def _won(value: object) -> str:
    return f"₩{value:,}" if isinstance(value, int) else "미표기"


def render_markdown(report: dict[str, Any]) -> str:
    policy = report["policy"]
    groups = report["jobs"]
    lines = [
        "# 주말 인력업체 일자리 보고서",
        "",
        f"생성: {report['generated_at']}",
        f"대상: {policy['search_start']}–{policy['search_end']} 중 토·일만",
        f"최저 기준: 실제/주휴 제외 환산 시급 {_won(policy['minimum_actual_hourly_won'])} 이상",
        "정렬: 실제/주휴 제외 시급 내림차순 → 검증 통근시간 오름차순",
        "",
        "> `실제 시급`은 주휴수당을 제외한 비교 기준입니다. `주휴 적용 시급`은 "
        "조건 충족 시에만 가능한 별도 참고값이며 최저시급 필터나 순위에 사용하지 않습니다.",
        "",
        f"총 {len(groups)}개 근무 후보 · 교차업체 중복 {report['counts']['cross_agency_duplicates']}개 "
        f"· 신규 {report['counts']['new']}개 · 변경 {report['counts']['changed']}개",
        "",
    ]
    if not groups:
        lines.extend(("현재 조건을 충족하는 공개 모집이 없습니다.", ""))
    for job in groups:
        commute = (
            f"{job['transit_minutes']}분"
            if isinstance(job.get("transit_minutes"), int)
            else "미검증"
        )
        agencies = ", ".join(SOURCE_LABELS.get(item, item) for item in job["agencies"])
        highest_agencies = ", ".join(
            SOURCE_LABELS.get(item, item)
            for item in job.get("highest_paying_agencies") or []
        )
        highest_label = (
            f"{highest_agencies} 공동 최고"
            if len(job.get("highest_paying_agencies") or []) > 1
            else f"{highest_agencies} 최고"
        )
        lines.extend(
            (
                f"## {job['rank']}. {job['employer']} · {job['work_date']}({job['weekday']}) {job['shift']}",
                "",
                f"- 실제 시급: **{_won(job['best_actual_hourly_won'])}** "
                f"({highest_label})",
                f"- 공고 중 최고 주휴 적용표기: {_won(job['best_weekly_allowance_hourly_won'])}",
                f"- 통근: {commute} · 역/지역: {job.get('station') or job.get('area') or '미표기'}",
                f"- 역할: {job['role_category']} · 확인 업체: {agencies}",
                f"- 교차업체 비교: {'예' if job['cross_agency_duplicate'] else '아니오'}",
                "",
                "| 업체 | 실제 시급 | 주휴 적용 시급 | 역할/상세 | 링크 |",
                "|---|---:|---:|---|---|",
            )
        )
        for offer in job["offers"]:
            pay = offer["pay"]
            warning = "; ".join(pay.get("warnings") or [])
            details = str(offer["title"]).replace("|", "/")
            if warning:
                details += f" ⚠ {warning}"
            if (offer.get("metadata") or {}).get("source_snapshot_stale"):
                details += " ⚠ 최근 소스 스냅샷 재사용"
            label = SOURCE_LABELS.get(offer["agency"], offer["agency"])
            lines.append(
                f"| {label} | {_won(pay.get('actual_hourly_won'))} | "
                f"{_won(pay.get('weekly_allowance_hourly_won'))} | {details} | "
                f"[공고]({offer['source_url']}) |"
            )
        lines.append("")

    lines.extend(
        (
            "## 실행 상태",
            "",
            f"- 무결성 검사: {'통과' if report['integrity_checks_passed'] else '실패'}",
            f"- 소스 상태: {'모두 실시간' if report['all_sources_live'] else '최근 스냅샷 포함'}",
            f"- Kakao 호출: {report['routing']['kakao_calls_this_run']}회",
            f"- 위치 미확인: {report['routing']['unresolved_location_count']}곳",
            "- 이 보고서는 Albamon 보고서 및 Discord 알림 상태와 분리되어 있습니다.",
            "",
        )
    )
    for name, state in report["sources"].items():
        lines.append(
            f"- {SOURCE_LABELS.get(name, name)}: {state.get('status', 'unknown')} · "
            f"{state.get('fetched_offer_count', 0)}개 · {state.get('elapsed_seconds', 0)}초"
        )
    lines.append("")
    return "\n".join(lines)


def _csv_text(report: dict[str, Any]) -> str:
    buffer = io.StringIO(newline="")
    fieldnames = [
        "rank",
        "group_id",
        "work_date",
        "weekday",
        "employer",
        "shift",
        "role_category",
        "best_actual_hourly_won",
        "best_weekly_allowance_hourly_won",
        "best_agency",
        "highest_paying_agencies",
        "transit_minutes",
        "station",
        "area",
        "cross_agency_duplicate",
        "agencies",
        "source_urls",
    ]
    writer = csv.DictWriter(buffer, fieldnames=fieldnames)
    writer.writeheader()
    for job in report["jobs"]:
        writer.writerow(
            {
                **{key: job.get(key) for key in fieldnames},
                "agencies": ";".join(job["agencies"]),
                "highest_paying_agencies": ";".join(
                    job.get("highest_paying_agencies") or []
                ),
                "source_urls": ";".join(
                    offer["source_url"] for offer in job["offers"]
                ),
            }
        )
    return buffer.getvalue()


def write_report(prefix: Path, report: dict[str, Any]) -> dict[str, str]:
    paths = {
        "json": str(prefix.with_suffix(".json")),
        "csv": str(prefix.with_suffix(".csv")),
        "markdown": str(prefix.with_suffix(".md")),
    }
    atomic_write_text(
        Path(paths["json"]),
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
    )
    with atomic_text_writer(Path(paths["csv"]), newline="") as handle:
        handle.write(_csv_text(report))
    atomic_write_text(Path(paths["markdown"]), render_markdown(report))
    return paths
