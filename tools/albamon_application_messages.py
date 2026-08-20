#!/usr/bin/env python3
"""Build one ranked Albamon report with reasoning and Korean application messages.

The generator deliberately separates persuasive copy from unverified hard
requirements.  A message never claims a certificate, physical attribute, or
specialized work history unless the applicant has supplied that fact.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import re
from datetime import datetime
from pathlib import Path
from typing import Any

try:
    from tools.albamon_runtime import atomic_text_writer, atomic_write_text
    from tools.albamon_transit import (
        ORIGIN as TRANSIT_ORIGIN,
        cache_origin_matches,
    )
except ModuleNotFoundError:  # Support direct execution from the repository root.
    from albamon_runtime import atomic_text_writer, atomic_write_text
    from albamon_transit import ORIGIN as TRANSIT_ORIGIN, cache_origin_matches


DEFAULT_INPUT = Path(
    "artifacts/albamon/2026-08-01-combined-matched-summer-urgent.json"
)
DEFAULT_OUTPUT_PREFIX = Path("artifacts/albamon/2026-08-01-application-messages")
DEFAULT_DISPOSITIONS = Path("tools/albamon_application_dispositions.json")
DEFAULT_TRANSIT_CACHE = Path("tools/albamon_transit_cache.json")
DEFAULT_MESSAGE_CACHE = Path("artifacts/albamon/cache/application_messages.json")
TRANSIT_CUTOFF_MINUTES = 50
MESSAGE_RULESET_VERSION = 7
MESSAGE_CACHE_FIELDS = {
    "message",
    "message_origin",
    "role_family",
    "employer_priorities",
    "relevance_rationale",
    "profile_facts_used",
    "verify_before_sending",
    "optional_sentence_if_true",
    "fit_warning",
}

PAY_PRIORITY_MODEL = {
    "sort_mode": "strict lexicographic: reliable work-hour pay descending, then transit minutes ascending",
    "ordering": [
        "reliable work-hour equivalent compensation",
        f"public-transit minutes from {TRANSIT_ORIGIN['name']}",
        "schedule certainty, duration, requirements, and application friction",
    ],
    "pay_points": "0-80; one point per KRW 100 above KRW 10,320/hour",
    "commute_points": "0-20; scaled across routes admitted by the applicable commute policy",
    "commute_eligibility": (
        "a conservative 20-minute walking-radius estimate from one of six preferred "
        "stations for weekday evening-only jobs; otherwise 50 minutes inclusive "
        "from home in Seoul, with pay-dependent extensions "
        "up to 90 minutes only in Yongsan and Yeongdeungpo"
    ),
    "tier_thresholds": {"A": 90, "B": 75, "C": 60, "D": 0},
}

BASE_COMMUTE_LIMIT_MINUTES = 50
EVENING_STATION_AREA_LIMIT_MINUTES = 20
EVENING_WALK_SPEED_KMH = 4.8
EVENING_WALK_ROUTE_FACTOR = 1.2
EVENING_STATION_COORDINATES = {
    "역삼역": (37.500658, 127.036430),
    "강남역": (37.497952, 127.027619),
    "송파역": (37.499703, 127.112183),
    "잠실역": (37.513305, 127.100129),
    "잠실새내역": (37.511687, 127.086162),
    "삼성역": (37.508844, 127.063160),
}
PREMIUM_COMMUTE_MAX_MINUTES = 90
PREMIUM_FIRST_TIER_PAY_WON = 12_000
PREMIUM_FIRST_TIER_MAX_PAY_WON = 15_000
PREMIUM_FIRST_TIER_MAX_MINUTES = 80
PREMIUM_FIRST_TIER_WON_PER_MINUTE = 100
PREMIUM_SECOND_TIER_MAX_PAY_WON = 18_000
PREMIUM_SECOND_TIER_WON_PER_MINUTE = 300
SEARCH_ZONE_LABELS = {
    "remote": "Remote · 재택",
    "core": "Core · 강남/송파",
    "seocho_corridor": "서초 · 방배/내방/반포/교대",
    "east_near_core": "East near-core · 강동/성동/광진",
    "premium_stretch": "Premium stretch · 용산/영등포",
    "outer_seoul": "Outer Seoul",
}
SEOUL_DISTRICT_RE = re.compile(
    r"(강남구|강동구|강북구|강서구|관악구|광진구|구로구|금천구|노원구|"
    r"도봉구|동대문구|동작구|마포구|서대문구|서초구|성동구|성북구|송파구|"
    r"양천구|영등포구|용산구|은평구|종로구|중구|중랑구)"
)

# These headlines are not comparable at their structured salary value. The
# employer-written detail gives the pay applicable to this weekend applicant.
PAY_COMPARISON_OVERRIDES: dict[int, dict[str, Any]] = {
    118119949: {
        "hourly_equivalent": 12_222,
        "basis": "상세 공고의 주말/주3 AR 일급 110,000원을 9시간으로 환산",
        "caution": "헤드라인 150,000원은 매니저 급여이며 주말 AR 공고 급여는 일급 110,000원",
        "confidence_penalty": 0,
    },
    118090446: {
        "hourly_equivalent": 10_320,
        "basis": "상세 공고의 기본 출연료 82,560원(8시간 기준)을 환산",
        "caution": "주급 1,000,000원은 보장액이 아니며 공고에 1년 회비 50,000원이 명시됨",
        "confidence_penalty": -10,
    },
    118207245: {
        "hourly_equivalent": 10_320,
        "basis": "상세 공고의 보조출연 기본 82,560원(8시간 보장액)을 환산",
        "caution": (
            "일급 300,000원은 장시간 촬영 때의 최대 예시이며 보장액이 아님; "
            "공고에 소개료가 발생할 수 있다고 명시됨"
        ),
        "confidence_penalty": -10,
    },
    118257004: {
        "hourly_equivalent": 18_000,
        "basis": "상세 공고에 명시된 주말 시급 18,000원",
        "caution": "Albamon 구조화 급여 15,000원은 평일 시급이며 주말 공고 급여는 18,000원",
        "confidence_penalty": 0,
    },
    118302036: {
        "hourly_equivalent": 18_333,
        "basis": "2일 총 330,000원을 공고의 유급 18시간(일 9시간)으로 환산",
        "caution": "12:00~23:00 중 휴게 2시간을 제외해 하루 9시간으로 계산",
        "confidence_penalty": 0,
    },
    117984582: {
        "hourly_equivalent": 15_972,
        "basis": "공고 제목의 주말 2일 총 230,000원을 일 7.2시간으로 환산",
        "caution": "구조화 급여가 건별이므로 제목의 총액과 근로시간을 기준으로 비교",
        "confidence_penalty": -5,
    },
}

# Albamon frequently republishes an unchanged ad under a new recruit number.
# Keep reviewed pay facts attached to the job itself so routine reposts do not
# silently fall back to a misleading structured salary.
SEMANTIC_PAY_COMPARISON_OVERRIDES: dict[tuple[str, str], dict[str, Any]] = {
    (
        "㈜밴스마케팅",
        "[평일 1.5 / 주말 1.8] 이태원역 인근 물티슈 배포 알바 구인",
    ): PAY_COMPARISON_OVERRIDES[118257004],
    (
        "이음",
        "[주말 부업] 포토부스 행사 매니저 & 스탭 모집",
    ): {
        "hourly_equivalent": 0,
        "basis": "상세 공고의 150,000원은 개인 일급이 아니라 팀당 총액",
        "caution": "매니저·스탭이 팀 총액을 협의 분배하므로 개인 보장 시급을 계산할 수 없음",
        "confidence_penalty": -10,
        "reliable": False,
        "shift_earnings_won": None,
    },
}


# Deliberately excludes the applicant's name, phone number, email, exact address,
# birth date, and profile/resume identifier.
PROFILE_FACTS: dict[str, dict[str, str]] = {}


def spec(
    position: str,
    pitch: str,
    facts: list[str],
    *,
    verify: list[str] | None = None,
    optional_sentence: str = "",
    fit_warning: str = "",
    role_family: str = "",
    employer_priorities: list[str] | None = None,
    relevance_rationale: str = "",
) -> dict[str, Any]:
    """Create one concise, sendable message specification."""

    return {
        "position": position,
        "pitch": pitch,
        "facts": facts,
        "verify": verify or ["지원 가능한 날짜와 근무시간"],
        "optional_sentence": optional_sentence,
        "fit_warning": fit_warning,
        "role_family": role_family,
        "employer_priorities": employer_priorities or [],
        "relevance_rationale": relevance_rationale,
    }


REGULAR_SPECS = {}


BMART_REQUIREMENTS = [
    "냉장·냉동 상품을 다루는 저온 환경 근무 가능 여부",
    "재고를 반복해서 들고 옮길 수 있는지",
    "본인 명의 급여 계좌 보유 여부",
    "09:00~18:00 또는 14:30~23:30 중 가능한 주말 타임",
]


SOFT_SKILLS = {
    "성실함", "책임감", "친절함", "인사성", "밝은 미소", "센스", "민첩함",
    "적극성", "자발성", "배려심", "예의", "자신감", "시간 준수", "꼼꼼함", "체력",
}

ROLE_PRIORITIES: dict[str, list[str]] = {
    "cleaning": ["구역별 청소", "위생 관리", "정리정돈"],
    "media_extra": ["집합시간 준수", "촬영 지시 이행", "복장 준비"],
    "child_education": ["체험 안내", "어린이 안전", "시설 정리"],
    "leaflet": ["배포 위치 준수", "정해진 수량 배포", "현장 보고"],
    "security_safety": ["고객 안내", "안전 관리", "이상 상황 보고"],
    "event": ["방문객 안내", "현장 동선 관리", "운영 지시 이행"],
    "hall_service": ["고객 응대", "서빙", "테이블 정리"],
    "kitchen": ["식재료 준비", "설거지", "위생 관리"],
    "cafe_bakery": ["고객 응대", "제품 포장·진열", "설거지·마감"],
    "food_service": ["주문 응대", "간단 조리", "매장 청결"],
    "retail": ["고객 응대", "상품 진열·정리", "POS 결제"],
    "front_desk": ["방문객 응대", "시설 점검", "문의 안내"],
    "office": ["자료 입력", "엑셀 정리", "오류 확인"],
    "general": ["업무 절차 준수", "시간 준수", "마무리 확인"],
}

FACT_RELEVANCE = {
    "restaurant": "음식점 경험을 위생·현장 적응과 연결",
    "hall_service": "[REMOVED_PRIVATE_PROFILE_7f43392d5e]을 고객 응대·서빙과 직접 연결",
    "health_certificate": "식품 취급 업무에 필요한 [REMOVED_PRIVATE_PROFILE_5d2d7223f1] 사실 사용",
    "events": "행사 운영 경험을 안내·동선·현장 대응과 연결",
    "english": "공고의 외국어 신호에만 영어 능력을 사용",
    "computer_excel": "전산·자료 업무에만 컴퓨터·엑셀 경험을 사용",
    "software": "정확한 데이터 처리나 기술 업무에만 개발 경력을 사용",
    "work_style": "공고가 요구하는 시간 준수·책임감에 연결",
    "jamsil": "인근 거주 또는 송파권 출퇴근 안정성과 연결",
}


def job_text(job: dict[str, Any]) -> str:
    values = [
        job.get("title"),
        job.get("company"),
        *(job.get("parts") or []),
        *(job.get("skills") or []),
        *(job.get("hard_requirements") or []),
    ]
    return " ".join(str(value) for value in values if value)


def classify_role(job: dict[str, Any]) -> str:
    """Choose the closest work family from the posting's duties, not its sector."""

    text = job_text(job)
    title = str(job.get("title") or "")
    parts = " ".join(str(value) for value in job.get("parts") or [])
    skills = " ".join(str(value) for value in job.get("skills") or [])
    if any(token in text for token in ("청소·미화", "미화스", "피부과 미화")):
        return "cleaning"
    if "보조출연·방청" in text or any(
        token in title for token in ("보조출연", "방청", "드라마", "영화", "홍보영상")
    ):
        return "media_extra"
    if any(token in text for token in ("유아·유치원", "교육·강사", "어린이박물관", "교육지도")):
        return "child_education"
    if "전단지" in text:
        return "leaflet"
    if any(
        token in text
        for token in (
            "주차유도", "보안·경비", "안전요원", "안전 관리", "서비스가드",
            "입출입 통제", "수신호", "입장안내",
        )
    ):
        return "security_safety"
    if any(token in title for token in ("판매크루", "판매 크루", "판매직")):
        return "retail"
    if any(token in parts for token in ("결혼·연회", "이벤트·행사스텝", "전시·컨벤션")) or any(
        token in title for token in ("운영스텝", "운영요원", "박람회", "프로모터", "행사스탭")
    ):
        return "event"
    if any(token in title for token in ("설거지", "식기세척")):
        return "kitchen"
    if "주방" in title or re.search(r"(?:^|\s)베이커(?:\s|$)", title):
        return "kitchen"
    if any(token in title for token in ("홀서빙", "홀 서빙", "홀 서비스", "연회장")) or "서빙" in parts:
        return "hall_service"
    if any(
        token in title
        for token in ("모텔카운터", "모텔 카운터", "호텔프런트", "호텔 프런트", "호텔프론트", "호텔 프론트")
    ):
        return "front_desk"
    kitchen_signal = any(
        token in f"{title} {skills}"
        for token in ("주방", "조리", "식재료", "설거지", "세척", "베이커")
    )
    cafe_signal = any(
        token in text
        for token in ("바리스타", "카페", "커피전문점", "베이커리", "디저트", "도넛", "아이스크림")
    )
    if kitchen_signal and not cafe_signal:
        return "kitchen"
    if cafe_signal:
        return "cafe_bakery"
    if any(
        token in text
        for token in (
            "일반음식점", "패스트푸드점", "음식점", "레스토랑", "샤브퀴진",
            "태극당", "다트플렉스",
        )
    ):
        return "food_service"
    if any(token in text for token in ("안내데스크·리셉션", "피트니스", "헬스장", "노래방", "PC방")):
        return "front_desk"
    if any(
        token in text
        for token in (
            "매장관리·판매", "캐셔·카운터", "편의점", "백화점·쇼핑몰",
            "의류·잡화", "뷰티·헬스스토어", "상품 판매", "상품 진열",
        )
    ):
        return "retail"
    if any(token in text for token in ("사무", "엑셀", "자료 입력", "전산", "코딩", "개발")):
        return "office"
    return "general"


def employer_priorities(job: dict[str, Any], role: str) -> list[str]:
    title = str(job.get("title") or "")
    if role == "front_desk" and any(
        token in title for token in ("모텔카운터", "모텔 카운터", "호텔프런트", "호텔 프런트", "호텔프론트", "호텔 프론트")
    ):
        return ["카운터 고객 응대", "입·퇴실 및 예약 확인", "야간 인수인계"]
    duties = [
        str(value).strip()
        for value in job.get("skills") or []
        if str(value).strip() and str(value).strip() not in SOFT_SKILLS
    ]
    if duties:
        return duties[:4]
    if "오픈런" in title:
        return ["집합시간 준수", "대기 순서·위치 준수", "현장 담당자 지시 이행"]
    if role == "event":
        if any(token in title for token in ("박람회", "전시", "문화")):
            return ["접수·입장 안내", "부스·프로그램 안내", "관람 동선 정리"]
        if any(token in title for token in ("포터", "예도", "웨딩", "컨벤션")):
            return ["하객 맞이", "예식·연회 동선 안내", "진행 신호 준수"]
        if "팝업" in title:
            return ["입장·대기열 안내", "운영 규칙 설명", "현장 정리"]
    if role == "security_safety":
        if any(token in title for token in ("주차", "수신호", "유도")):
            return ["차량·보행자 동선 분리", "수신호", "혼잡 상황 보고"]
        if any(token in title for token in ("서비스가드", "입장안내", "고객안내")):
            return ["입장·대기열 안내", "고객 동선 관리", "이상 상황 보고"]
    if role == "front_desk" and any(token in title for token in ("헬스장", "피트니스")):
        return ["회원 응대", "시설 점검", "마감 정리"]
    return ROLE_PRIORITIES[role]


def role_spec(job: dict[str, Any]) -> tuple[str, list[str]]:
    """Write a role-grounded pitch and return only the evidence it actually uses."""

    role = classify_role(job)
    priorities = employer_priorities(job, role)
    duty_text = "·".join(priorities[:3])
    if role == "cleaning":
        pitch = (
            "음식점 근무에서 위생과 정리정돈을 지키며 일했습니다. "
            "정해진 청소 구역과 순서를 누락 없이 확인하고, 이용객 동선을 방해하지 않도록 "
            f"{duty_text} 업무를 꼼꼼하게 마무리하겠습니다."
        )
        facts = ["restaurant", "work_style"]
    elif role == "media_extra":
        pitch = (
            "시간 약속과 현장 지시를 정확히 지키는 편입니다. 촬영 전 안내되는 집합시간과 "
            "복장을 미리 확인하고, 대기·이동·반복 촬영 지시에 맞춰 현장 흐름을 방해하지 않겠습니다."
        )
        facts = ["work_style"]
    elif role == "child_education":
        pitch = (
            "행사에서 방문객에게 프로그램과 이용 순서를 알기 쉽게 안내한 경험이 있습니다. "
            f"{duty_text}를 먼저 숙지하고, 어린이가 안전하게 참여하도록 차분히 설명하고 확인하겠습니다."
        )
        facts = ["events", "work_style"]
    elif role == "leaflet":
        pitch = (
            "행사 운영 중 낯선 방문객에게 먼저 밝게 안내하고 현장 상황을 공유해 왔습니다. "
            "지정된 배포 위치와 수량을 지키고, 중복·누락 없이 진행 상황을 정확히 보고하겠습니다."
        )
        facts = ["events", "work_style"]
    elif role == "security_safety":
        pitch = (
            "행사 방문객 안내 경험이 있어 혼잡한 상황에서도 짧고 분명하게 동선을 설명할 수 있습니다. "
            f"{duty_text} 기준을 먼저 숙지하고, 이상 상황은 임의로 넘기지 않고 즉시 공유하겠습니다."
        )
        facts = ["events", "work_style"]
    elif role == "event":
        pitch = (
            "국제 행사 부스 운영과 방문객 안내 경험이 있어 현장 안내, 문의 응대와 동선 정리에 익숙합니다. "
            f"행사 전에 {duty_text} 절차를 숙지하고, 방문객에게 같은 내용을 일관되고 정확하게 전달하겠습니다."
        )
        facts = ["events", "work_style"]
    elif role == "hall_service":
        pitch = (
            "[REMOVED_PRIVATE_PROFILE_7f43392d5e]으로 주문 확인, 서빙, 테이블 정리와 피크타임 협업에 익숙합니다. "
            f"{duty_text} 순서를 빠르게 파악하고, 요청을 놓치지 않도록 테이블과 홀 상황을 계속 확인하겠습니다."
        )
        facts = ["restaurant", "hall_service", "work_style"]
    elif role == "kitchen":
        pitch = (
            "[REMOVED_PRIVATE_PROFILE_8f8253601a]이 있고 보건증도 보유하고 있습니다. "
            f"{duty_text}의 작업 순서와 위생 기준을 먼저 익혀, 바쁜 시간에도 조리 동선이 막히지 않게 보조하겠습니다."
        )
        facts = ["restaurant", "health_certificate", "work_style"]
    elif role == "cafe_bakery":
        pitch = (
            "음식점과 [REMOVED_PRIVATE_PROFILE_7f43392d5e]으로 고객 응대, 위생 관리와 마감 정리에 익숙하며 보건증도 보유하고 있습니다. "
            f"{duty_text}의 매장별 순서와 레시피를 정확히 익혀, 배운 업무부터 정확하고 안정적으로 수행하겠습니다."
        )
        facts = ["restaurant", "hall_service", "health_certificate", "work_style"]
    elif role == "food_service":
        pitch = (
            "음식점 아르바이트와 [REMOVED_PRIVATE_PROFILE_7f43392d5e]이 있고 보건증도 보유하고 있습니다. "
            f"{duty_text}의 순서를 빠르게 익히고, 주문과 식품 위생을 함께 확인하며 피크타임 흐름에 맞춰 움직이겠습니다."
        )
        facts = ["restaurant", "hall_service", "health_certificate", "work_style"]
    elif role == "retail":
        pitch = (
            "행사 부스에서 방문객을 응대하고 필요한 정보를 정확히 안내한 경험이 있습니다. "
            f"{duty_text} 기준을 먼저 익히고, 고객 문의에는 확인된 상품 정보로 정확하게 안내하겠습니다."
        )
        facts = ["events", "work_style"]
    elif role == "front_desk":
        pitch = (
            "행사 운영과 방문객 안내 경험으로 처음 오는 분의 문의를 듣고 필요한 위치와 절차를 설명해 왔습니다. "
            f"{duty_text} 기준을 숙지하고, 시설 이상이나 인계 사항을 빠뜨리지 않고 기록·공유하겠습니다."
        )
        facts = ["events", "work_style"]
    elif role == "office":
        pitch = (
            "컴퓨터·엑셀·코딩 업무 경험이 있어 자료를 구조적으로 정리하고 오류를 확인하는 데 익숙합니다. "
            f"{duty_text} 절차와 파일 규칙을 먼저 확인하고, 처리 결과를 다시 대조해 정확하게 마무리하겠습니다."
        )
        facts = ["software", "computer_excel", "work_style"]
    else:
        pitch = (
            "시간 약속과 맡은 업무의 마무리를 중요하게 생각합니다. "
            f"{duty_text}의 기준과 보고 순서를 먼저 확인하고, 불명확한 부분은 바로 질문해 정확히 수행하겠습니다."
        )
        facts = ["work_style"]

    text = job_text(job)
    if any(token in text for token in ("외국어", "영어", "통역", "외국인 고객")):
        pitch += " 영어는 원어민 수준이라 외국인 고객 문의도 정확하게 응대할 수 있습니다."
        facts.append("english")
    if "인근 거주" in text and job.get("target_district") == "송파구":
        pitch += " [REMOVED_PRIVATE_PROFILE_2e9ee7e3fd]라 송파권 출퇴근도 안정적입니다."
        facts.append("jamsil")
    return pitch, facts


def required_application_questions(job: dict[str, Any]) -> list[str]:
    """Return mandatory screening prompts that must be answered before applying."""

    questions: list[str] = []
    for item in job.get("pre_application_questions") or []:
        if not isinstance(item, dict) or not item.get("required"):
            continue
        title = " ".join(str(item.get("title") or "").split())
        if title and title not in questions:
            questions.append(title)
    return questions


def report_hard_requirements(job: dict[str, Any]) -> list[str]:
    """Keep posting requirements and mandatory application prompts together."""

    requirements = [str(value) for value in job.get("hard_requirements") or []]
    for question in required_application_questions(job):
        requirement = f"지원서 필수 질문: {question}"
        if requirement not in requirements:
            requirements.append(requirement)
    return requirements


def requirements_for_message(job: dict[str, Any], role: str) -> tuple[list[str], str, str]:
    requirements = [str(value) for value in job.get("hard_requirements") or []]
    verify: list[str] = []
    optional = ""
    unsatisfied: list[str] = []
    for requirement in requirements:
        lowered = requirement.lower()
        if "health certificate" in lowered or "보건증" in requirement:
            verify.append("보건증 제출 필요 시점")
        elif "at least age" in lowered or "must be at least" in lowered or "minors" in lowered:
            continue
        elif "specified event outfit" in lowered:
            verify.append("지정 복장 준비 가능 여부")
            optional = "공고의 지정 복장도 준비 가능합니다."
            unsatisfied.append("지정 복장 준비")
        elif "relevant experience" in lowered:
            verify.append(f"필수 경력 충족 여부: {requirement}")
            unsatisfied.append("공고가 요구하는 관련 실무 경력")
        else:
            verify.append(f"하드 요건 충족 여부: {requirement}")
            unsatisfied.append(requirement)
    for question in required_application_questions(job):
        verify.append(f"지원서 필수 질문 확인: {question}")
        if "정장" in question and "소지" in question:
            optional = "검은색 또는 다크네이비 정장을 보유하고 있습니다."
            unsatisfied.append("검은색 또는 다크네이비 정장 보유 여부")
    warning = (
        "지원 전 사실 확인이 필요한 하드 요건: " + "; ".join(unsatisfied)
        if unsatisfied
        else ""
    )
    return verify, optional, warning


def known_profile_mismatch(job: dict[str, Any], role: str) -> str:
    """Return a verified applicant mismatch that should not remain actionable."""

    title = str(job.get("title") or "")
    experience_only = bool(
        re.search(r"경력(?:자|직)\s*(?:만|지원\s*요망|필수)", title)
    )
    if experience_only and role == "cafe_bakery":
        return "공고가 카페 경력자만 요구하지만 현재 확인된 카페 실무 경력이 없음"
    if experience_only and role == "front_desk":
        return "공고가 관련 경력자만 요구하지만 현재 확인된 해당 실무 경력이 없음"
    if "Powerboat/yacht/deck-officer licence" in (
        job.get("certificate_requirements") or []
    ):
        return "공고 필수인 동력수상레저 1급·요트·6급 항해사 자격증 보유 사실이 확인되지 않음"
    return ""


def bmart_spec(job: dict[str, Any]) -> dict[str, Any]:
    """Build the branch-specific B-Mart pitch."""

    company = str(job["company"])
    branch = company.removeprefix("배민B마트 ").strip() or company
    local_sentence = ""
    facts = ["work_style"]
    if job.get("target_district") == "송파구":
        local_sentence = " [REMOVED_PRIVATE_PROFILE_2e9ee7e3fd]로 송파권 출퇴근도 안정적입니다."
        facts.append("jamsil")
    return spec(
        f"{company} 단기 일일크루",
        "시간 준수와 맡은 업무의 마무리를 중요하게 생각합니다. "
        f"{branch}에서 상품 코드·수량·보관 구역을 대조해 피킹·포장 오류를 줄이고, "
        "완료한 재고는 다음 작업자가 바로 확인할 수 있게 정리하겠습니다."
        + local_sentence,
        facts,
        verify=BMART_REQUIREMENTS,
        optional_sentence="저온 환경 근무와 반복적인 재고 운반이 가능하며, 본인 명의 급여 계좌도 보유하고 있습니다.",
        fit_warning="저온 근무·반복 운반·본인 명의 계좌가 모두 하드 요건입니다.",
        role_family="logistics",
        employer_priorities=["피킹·포장", "재고 정리", "상품 코드·수량 확인"],
        relevance_rationale="책임감과 시간 준수만 사용하고 개발·음식점 경력은 제외",
    )


def coupang_spec(job: dict[str, Any]) -> dict[str, Any]:
    """Build the location-cautious Coupang pitch."""

    return spec(
        "쿠팡 주말 단순 소분·피킹 스태프",
        "시간과 안전수칙을 정확히 지키고 맡은 구역을 끝까지 마무리하겠습니다. 상품 구분표와 수량을 먼저 확인한 뒤 소분·피킹하고, 파손이나 수량 차이가 있으면 임의 처리하지 않고 바로 보고하겠습니다.",
        ["work_style"],
        verify=["실제 물류센터 주소", "송파 집결지에서 출발하는 셔틀 노선·시간", "원하는 주말 날짜와 실제 근무시간", "중량물 취급 범위"],
        fit_warning="공고의 송파구 표기는 실제 센터가 아니라 셔틀·집결지일 수 있으므로 센터 주소 확인이 필수입니다.",
        role_family="logistics",
        employer_priorities=["단순 소분", "피킹", "안전수칙 준수"],
        relevance_rationale="물류 경력을 꾸미지 않고 확인 절차·시간 준수만 강조",
    )


def compose_message(item: dict[str, Any]) -> str:
    """Render a concise Korean message from a specification."""

    return f"안녕하세요. {item['pitch']} 감사합니다."


def fallback_regular_spec(job: dict[str, Any]) -> dict[str, Any]:
    """Create a posting-grounded message without unrelated résumé filler."""

    company = str(job.get("company") or "해당 업체")
    role = classify_role(job)
    priorities = employer_priorities(job, role)
    pitch, facts = role_spec(job)

    schedule = " ".join(
        value
        for value in (
            str(job.get("work_days") or ""),
            str(job.get("work_time") or ""),
            str(job.get("work_time_option") or ""),
        )
        if value and value != "시간협의"
    )
    verify = [f"실제 지원 가능한 일정 ({schedule or '공고 상세 시간 확인 필요'})"]
    requirement_checks, optional, warning = requirements_for_message(job, role)
    verify.extend(requirement_checks)
    return spec(
        f"{company} 공고",
        pitch,
        facts,
        verify=verify,
        optional_sentence=optional,
        fit_warning=warning,
        role_family=role,
        employer_priorities=priorities,
        relevance_rationale="; ".join(FACT_RELEVANCE[key] for key in facts),
    )


def load_dispositions(path: Path) -> dict[int, dict[str, str]]:
    """Load persistent user choices for jobs that should not be actionable yet."""

    if not path.exists():
        return {}
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("application dispositions must be a JSON object")
    dispositions: dict[int, dict[str, str]] = {}
    for recruit_no, value in payload.items():
        if not isinstance(value, dict):
            raise ValueError(f"invalid disposition for {recruit_no}")
        status = str(value.get("status") or "")
        if status not in {"maybe_later", "do_not_apply"}:
            raise ValueError(f"unsupported disposition status for {recruit_no}: {status}")
        dispositions[int(recruit_no)] = {
            "status": status,
            "reason": str(value.get("reason") or "사용자 지정 보류"),
        }
    return dispositions


def load_transit_cache(path: Path | None) -> dict[int, dict[str, Any]]:
    """Load privacy-minimized bus/train commute results keyed by recruit ID."""

    if path is None or not path.exists():
        return {}
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict) or not cache_origin_matches(payload):
        raise ValueError(
            "transit cache was measured from a different origin; "
            "refresh routes before building application messages"
        )
    raw_jobs = payload.get("jobs") if isinstance(payload, dict) else None
    if not isinstance(raw_jobs, dict):
        raise ValueError("transit cache must contain a jobs object")
    return {
        int(recruit_no): value
        for recruit_no, value in raw_jobs.items()
        if isinstance(value, dict)
    }


def numeric_pay(record: dict[str, Any]) -> int:
    digits = re.sub(r"\D", "", str(record.get("salary") or ""))
    return int(digits) if digits else 0


def scheduled_shift_hours(record: dict[str, Any]) -> tuple[float | None, str]:
    """Return the full scheduled span without guessing an unstated shift."""

    def spans(value: str) -> list[float]:
        result: list[float] = []
        for times in re.finditer(
            r"(\d{1,2}):(\d{2})\s*[~～-]\s*(\d{1,2}):(\d{2})",
            value,
        ):
            start = int(times.group(1)) + int(times.group(2)) / 60
            end = int(times.group(3)) + int(times.group(4)) / 60
            if end <= start:
                end += 24
            hours = end - start
            if 0 < hours <= 18:
                result.append(hours)
        return result

    work_time_spans = spans(str(record.get("work_time") or ""))
    if work_time_spans:
        return max(work_time_spans), "표시 근무시간 전체 구간"

    selectable_spans = [
        hours
        for field in ("weekend_shift_options", "late_night_shift_options")
        for option in (record.get(field) or [])
        for hours in spans(str(option))
    ]
    if selectable_spans:
        longest = max(selectable_spans)
        return longest, f"상세 선택 가능 근무조 중 최장 {longest:g}시간 구간"
    return None, "근무시간 미확인"


def comparable_shift_hours(record: dict[str, Any]) -> tuple[float | None, str]:
    """Return conservative paid hours for normalizing a daily/monthly amount."""

    title = str(record.get("title") or "")
    option = str(record.get("work_time_option") or "")
    explicit = re.search(r"실근로\s*(\d+(?:\.\d+)?)\s*시간", title)
    if explicit:
        return float(explicit.group(1)), "제목에 명시된 실근로시간"
    range_hours = re.search(r"(\d+(?:\.\d+)?)\s*[~～-]\s*(\d+(?:\.\d+)?)\s*시간", option)
    if range_hours:
        return float(range_hours.group(2)), "근무시간 범위의 최대치로 보수적 환산"
    hours, span_basis = scheduled_shift_hours(record)
    if hours is None:
        return None, span_basis
    break_match = re.search(r"휴게시간\s*(\d+)\s*분", option)
    if break_match:
        hours -= int(break_match.group(1)) / 60
        if hours <= 0:
            return None, "휴게시간이 표시 근무시간 이상이라 환산 불가"
        return hours, "표시 근무시간에서 명시된 휴게시간을 제외"
    return hours, f"{span_basis}; 명시된 휴게시간 없음"


def comparable_hourly_pay(record: dict[str, Any]) -> dict[str, Any]:
    """Normalize mixed salary units without treating headline maxima as guarantees."""

    recruit_no = int(record.get("recruit_no") or 0)
    override = PAY_COMPARISON_OVERRIDES.get(recruit_no)
    if not override:
        semantic_key = (
            " ".join(str(record.get("company") or "").split()),
            " ".join(str(record.get("title") or "").split()),
        )
        override = SEMANTIC_PAY_COMPARISON_OVERRIDES.get(semantic_key)
    if override:
        reviewed = dict(override)
        reviewed.setdefault("reliable", True)
        reviewed.setdefault("paid_shift_hours", None)
        reviewed.setdefault("shift_span_hours", None)
        reviewed.setdefault("shift_earnings_won", None)
        return reviewed
    amount = numeric_pay(record)
    salary_type = str(record.get("salary_type") or "")
    hours, hours_basis = comparable_shift_hours(record)
    confidence_penalty = 0
    reliable = True
    shift_span_hours, _span_basis = scheduled_shift_hours(record)
    paid_shift_hours = hours
    shift_earnings_won: int | None = None
    if salary_type == "시급":
        equivalent = amount
        basis = "공고 시급"
        paid_duration_verified = bool(
            hours is not None and "명시된 휴게시간 없음" not in hours_basis
        )
        if amount and paid_duration_verified:
            shift_earnings_won = round(amount * hours)
    elif salary_type == "일급":
        if hours is None:
            equivalent = 0
            basis = "근무시간 미확인으로 일급 시급환산 제외"
            reliable = False
            confidence_penalty = -5
        else:
            equivalent = round(amount / hours) if amount else 0
            basis = f"공고 일급을 {hours:g}시간으로 환산 ({hours_basis})"
        shift_earnings_won = amount or None
    elif salary_type == "월급":
        weekly_hours = re.search(r"주\s*(\d+(?:\.\d+)?)\s*시간", str(record.get("title") or ""))
        if weekly_hours:
            monthly_hours = float(weekly_hours.group(1)) * 52 / 12
            basis = f"공고의 주 {weekly_hours.group(1)}시간을 월환산"
        elif hours is not None and "주말" in str(record.get("work_days") or ""):
            monthly_hours = hours * 2 * 52 / 12
            basis = f"토·일 각 {hours:g}시간을 월환산 ({hours_basis})"
        else:
            monthly_hours = None
            basis = "주간 근무시간 미확인으로 월급 시급환산 제외"
            reliable = False
            confidence_penalty = -5
        equivalent = round(amount / monthly_hours) if monthly_hours else 0
    elif salary_type == "주급":
        equivalent = 0
        basis = "주간 근무시간 미확인으로 주급 시급환산 제외"
        reliable = False
        confidence_penalty = -10
    else:
        equivalent = 0
        basis = "비교 가능한 급여 단위 미확인"
        reliable = False
        confidence_penalty = -10
    if not amount:
        reliable = False
    return {
        "hourly_equivalent": max(0, equivalent),
        "basis": basis,
        "caution": "" if reliable else "환산에 필요한 근무시간이 확인되지 않아 급여 순위와 Pareto에서 제외",
        "confidence_penalty": confidence_penalty,
        "reliable": reliable,
        "paid_shift_hours": paid_shift_hours,
        "shift_span_hours": shift_span_hours,
        "shift_earnings_won": shift_earnings_won,
    }


def door_to_door_hourly_pay(
    record: dict[str, Any], pay: dict[str, Any]
) -> dict[str, Any]:
    """Return earnings per scheduled span plus the verified round-trip commute."""

    earnings = pay.get("shift_earnings_won")
    span_hours = pay.get("shift_span_hours")
    remote_job = bool(
        record.get("remote_job")
        or "remote_short_term" in (record.get("eligibility_lanes") or [])
    )
    commute = 0 if remote_job else record.get("transit_minutes")
    if not isinstance(earnings, int) or earnings <= 0:
        return {"hourly_equivalent": None, "total_minutes": None, "basis": "교대 총수입 미확인"}
    if not isinstance(span_hours, (int, float)) or span_hours <= 0:
        return {"hourly_equivalent": None, "total_minutes": None, "basis": "정확한 근무시간 미확인"}
    if not isinstance(commute, int) or commute < 0:
        return {"hourly_equivalent": None, "total_minutes": None, "basis": "왕복 통근시간 미검증"}
    total_minutes = round(span_hours * 60) + 2 * commute
    if total_minutes <= 0:
        return {"hourly_equivalent": None, "total_minutes": None, "basis": "총투입시간 계산 불가"}
    return {
        "hourly_equivalent": round(earnings * 60 / total_minutes),
        "total_minutes": total_minutes,
        "basis": "교대 총수입 ÷ (전체 근무 구간 + 검증된 왕복 통근)",
    }


def classify_search_zone(record: dict[str, Any]) -> tuple[str, str]:
    """Group Seoul jobs into practical search zones from verified job text."""

    if record.get("remote_job") or "remote_short_term" in (
        record.get("eligibility_lanes") or []
    ):
        return "remote", SEARCH_ZONE_LABELS["remote"]
    district = str(record.get("target_district") or "").strip()
    location_text = " ".join(
        [district]
        + [
            str(record.get(field) or "")
            for field in ("area", "working_address", "title")
        ]
    )
    match = SEOUL_DISTRICT_RE.search(location_text)
    if match:
        district = match.group(1)
    if district in {"강남구", "송파구"}:
        zone = "core"
    elif district == "서초구":
        zone = "seocho_corridor"
    elif district in {"강동구", "성동구", "광진구"}:
        zone = "east_near_core"
    elif district in {"용산구", "영등포구"}:
        zone = "premium_stretch"
    else:
        zone = "outer_seoul"
    return zone, SEARCH_ZONE_LABELS[zone]


def _seoul_coordinates(*sources: dict[str, Any]) -> tuple[float, float] | None:
    for source in sources:
        try:
            latitude = float(source.get("latitude"))
            longitude = float(source.get("longitude"))
        except (TypeError, ValueError):
            continue
        if 37.3 <= latitude <= 37.75 and 126.75 <= longitude <= 127.25:
            return latitude, longitude
    return None


def _haversine_km(
    first: tuple[float, float], second: tuple[float, float]
) -> float:
    first_latitude, first_longitude = map(math.radians, first)
    second_latitude, second_longitude = map(math.radians, second)
    latitude_delta = second_latitude - first_latitude
    longitude_delta = second_longitude - first_longitude
    haversine = (
        math.sin(latitude_delta / 2) ** 2
        + math.cos(first_latitude)
        * math.cos(second_latitude)
        * math.sin(longitude_delta / 2) ** 2
    )
    return 6_371.0088 * 2 * math.asin(min(1.0, math.sqrt(haversine)))


def evening_station_proximity(
    record: dict[str, Any], transit: dict[str, Any]
) -> dict[str, Any]:
    """Estimate a conservative walk from the worksite to the nearest target station."""

    coordinates = _seoul_coordinates(transit, record)
    if coordinates is None:
        return {
            "evening_station_area_status": "unverified",
            "evening_station_area_eligible": False,
            "evening_station_area_limit_minutes": EVENING_STATION_AREA_LIMIT_MINUTES,
            "evening_nearest_station": "",
            "evening_station_distance_km": None,
            "evening_station_walk_minutes": None,
            "evening_station_area_basis": (
                "worksite coordinates unavailable; station-area proximity requires "
                "WebUI verification"
            ),
        }
    nearest_station, direct_distance_km = min(
        (
            (station, _haversine_km(coordinates, station_coordinates))
            for station, station_coordinates in EVENING_STATION_COORDINATES.items()
        ),
        key=lambda item: item[1],
    )
    estimated_route_km = direct_distance_km * EVENING_WALK_ROUTE_FACTOR
    walking_minutes = math.ceil(
        estimated_route_km / EVENING_WALK_SPEED_KMH * 60
    )
    eligible = walking_minutes <= EVENING_STATION_AREA_LIMIT_MINUTES
    return {
        "evening_station_area_status": "estimated",
        "evening_station_area_eligible": eligible,
        "evening_station_area_limit_minutes": EVENING_STATION_AREA_LIMIT_MINUTES,
        "evening_nearest_station": nearest_station,
        "evening_station_distance_km": round(direct_distance_km, 2),
        "evening_station_walk_minutes": walking_minutes,
        "evening_station_area_basis": (
            f"{nearest_station} 직선 {direct_distance_km:.2f}km에 보행 우회계수 "
            f"{EVENING_WALK_ROUTE_FACTOR:.1f} 적용: 약 {walking_minutes}분"
        ),
    }


def commute_policy_for_job(
    record: dict[str, Any],
    transit: dict[str, Any],
    *,
    hourly_pay: int | None = None,
) -> dict[str, Any]:
    """Apply the evening, base, or premium-district inclusive commute cutoff."""

    zone, zone_label = classify_search_zone(record)
    if zone == "remote":
        return {
            "search_zone": zone,
            "search_zone_label": zone_label,
            "commute_limit_minutes": 0,
            "commute_extension_minutes": 0,
            "commute_policy_reason": "Remote work: no commute required",
            "commute_policy_eligible": True,
            "pay_adjusted_commute": False,
            "distance_excluded": False,
            "distance_exclusion_reason": "",
            "evening_station_area_status": "not_applicable",
            "evening_station_area_eligible": None,
            "evening_station_area_limit_minutes": None,
            "evening_nearest_station": "",
            "evening_station_distance_km": None,
            "evening_station_walk_minutes": None,
            "evening_station_area_basis": "",
        }
    lanes = set(record.get("eligibility_lanes") or [])
    evening_only = (
        "songpa_late_night" in lanes and "weekend_short_term" not in lanes
    )
    limit = BASE_COMMUTE_LIMIT_MINUTES
    reliable_hourly_pay = max(0, int(hourly_pay or 0))
    if not evening_only and zone == "premium_stretch":
        if reliable_hourly_pay >= PREMIUM_SECOND_TIER_MAX_PAY_WON:
            limit = PREMIUM_COMMUTE_MAX_MINUTES
        elif reliable_hourly_pay >= PREMIUM_FIRST_TIER_MAX_PAY_WON:
            limit = min(
                PREMIUM_COMMUTE_MAX_MINUTES,
                PREMIUM_FIRST_TIER_MAX_MINUTES
                + (
                    reliable_hourly_pay - PREMIUM_FIRST_TIER_MAX_PAY_WON
                )
                // PREMIUM_SECOND_TIER_WON_PER_MINUTE,
            )
        elif reliable_hourly_pay >= PREMIUM_FIRST_TIER_PAY_WON:
            limit = min(
                PREMIUM_FIRST_TIER_MAX_MINUTES,
                BASE_COMMUTE_LIMIT_MINUTES
                + (reliable_hourly_pay - PREMIUM_FIRST_TIER_PAY_WON)
                // PREMIUM_FIRST_TIER_WON_PER_MINUTE,
            )

    minutes = transit.get("transit_minutes")
    route_verified = (
        transit.get("route_status") == "verified" and isinstance(minutes, int)
    )
    if route_verified:
        commute_excluded = int(minutes) > limit
    else:
        commute_excluded = bool(transit.get("distance_excluded"))
    station_proximity = (
        evening_station_proximity(record, transit)
        if evening_only
        else {
            "evening_station_area_status": "not_applicable",
            "evening_station_area_eligible": None,
            "evening_station_area_limit_minutes": None,
            "evening_nearest_station": "",
            "evening_station_distance_km": None,
            "evening_station_walk_minutes": None,
            "evening_station_area_basis": "",
        }
    )
    station_area_excluded = bool(
        evening_only and not station_proximity["evening_station_area_eligible"]
    )
    distance_excluded = commute_excluded or station_area_excluded
    pay_adjusted_commute = bool(
        route_verified
        and BASE_COMMUTE_LIMIT_MINUTES < int(minutes) <= limit
        and limit > BASE_COMMUTE_LIMIT_MINUTES
    )
    if evening_only:
        policy_reason = (
            "Evening station-area rule: estimated walk up to "
            f"{EVENING_STATION_AREA_LIMIT_MINUTES} min from 역삼·강남·송파·잠실·"
            f"잠실새내·삼성, plus home commute up to {limit} min"
        )
    elif zone == "premium_stretch" and limit > BASE_COMMUTE_LIMIT_MINUTES:
        policy_reason = (
            f"Premium commute limit: {limit} min inclusive at reliable comparable "
            f"hourly pay {reliable_hourly_pay:,} won"
        )
    else:
        policy_reason = f"Standard commute limit: {limit} min inclusive"
    exclusion_reason = str(transit.get("distance_exclusion_reason") or "")
    if station_area_excluded:
        if station_proximity["evening_station_area_status"] == "unverified":
            exclusion_reason = (
                "선호 역세권 20분 범위를 검증할 근무지 좌표가 없어 WebUI 확인 필요"
            )
        else:
            exclusion_reason = (
                f"가장 가까운 {station_proximity['evening_nearest_station']}까지 "
                f"보수적 도보 추정 {station_proximity['evening_station_walk_minutes']}분으로 "
                f"{EVENING_STATION_AREA_LIMIT_MINUTES}분 이하 역세권 기준 미충족"
            )
    elif route_verified:
        if commute_excluded:
            exclusion_reason = (
                f"{TRANSIT_ORIGIN['name']}에서 대중교통 최단 {minutes}분으로 "
                f"{limit}분 이하 기준 미충족"
            )
        else:
            exclusion_reason = ""
    return {
        "search_zone": zone,
        "search_zone_label": zone_label,
        "commute_limit_minutes": limit,
        "commute_extension_minutes": max(0, limit - BASE_COMMUTE_LIMIT_MINUTES),
        "commute_policy_reason": policy_reason,
        "commute_policy_eligible": not distance_excluded,
        "pay_adjusted_commute": pay_adjusted_commute,
        "distance_excluded": distance_excluded,
        "distance_exclusion_reason": exclusion_reason,
        **station_proximity,
    }


def assess_application_priority(
    record: dict[str, Any],
) -> tuple[int, str, list[str], list[str], dict[str, Any]]:
    """Score pay first, exact public-transit commute second, and fit third."""

    pay = comparable_hourly_pay(record)
    hourly = int(pay["hourly_equivalent"])
    total_time_pay = door_to_door_hourly_pay(record, pay)
    pay_points = max(0, min(80, round((hourly - 10_320) / 100)))
    minutes = record.get("transit_minutes")
    remote_job = bool(
        record.get("remote_job")
        or "remote_short_term" in (record.get("eligibility_lanes") or [])
    )
    commute_points = (
        20
        if remote_job
        else
        max(
            0,
            min(
                20,
                round((TRANSIT_CUTOFF_MINUTES - int(minutes)) / 2.5),
            ),
        )
        if isinstance(minutes, int) and minutes < TRANSIT_CUTOFF_MINUTES
        else 0
    )
    fit_points = int(pay.get("confidence_penalty") or 0)
    pay_reason = (
        f"{pay_points:+d} 급여 우선점수: 근무 환산시급 약 {hourly:,}원 ({pay['basis']})"
        if pay.get("reliable")
        else f"+0 급여 우선점수: {pay['basis']}"
    )
    reasons = [
        pay_reason,
        (
            f"{commute_points:+d} 통근 우선점수: 재택근무로 통근 없음"
            if remote_job
            else f"{commute_points:+d} 통근 우선점수: "
            f"{TRANSIT_ORIGIN['name']}에서 대중교통 {minutes}분"
            if isinstance(minutes, int)
            else "+0 통근 우선점수: 실제 근무지 기준 미검증"
        ),
    ]
    cautions: list[str] = []
    if record.get("pay_adjusted_commute"):
        cautions.append(
            f"급여 조정 통근 허용: {minutes}분 / {record.get('commute_limit_minutes')}분 이하"
        )
    if pay.get("caution"):
        cautions.append(str(pay["caution"]))

    def fit_adjust(points: int, label: str, *, caution: bool = False) -> None:
        nonlocal fit_points
        fit_points += points
        rendered = f"{points:+d} {label}"
        (cautions if caution else reasons).append(rendered)

    eligibility_lanes = set(record.get("eligibility_lanes") or [])
    if (
        "songpa_late_night" in eligibility_lanes
        and "weekend_short_term" not in eligibility_lanes
    ):
        fit_adjust(4, "역삼·강남·송파·잠실·잠실새내·삼성권 19:00~02:00 근무조 명시")
        cautions.append("02:00 퇴근 시 귀가 교통수단 별도 확인 필요")
    elif (
        "aug_15_17_single_day" in eligibility_lanes
        and "weekend_short_term" not in eligibility_lanes
    ):
        dates = ", ".join(record.get("target_date_options") or [])
        fit_adjust(4, f"{dates} 중 1일 독립 근무 가능 확인")
    else:
        weekend_basis = str(record.get("weekend_compatibility_basis") or "")
        weekend_explicit = (
            weekend_basis.startswith("weekend")
            or weekend_basis == "explicit weekdays"
        )
        fit_adjust(
            4 if weekend_explicit else 1,
            "토·일 일정 명시" if weekend_explicit else "주말 협의 가능",
        )
    duration_status = str(record.get("duration_review_status") or "")
    duration_adjustments = {
        "verified_shorter": (4, "1개월 미만 근무기간 확인", False),
        "not_found": (2, "1개월 이상 최소기간 미발견", False),
        "negotiable": (-2, "근무기간 협의 필요", True),
        "not_stated": (-3, "숫자 최소 근무기간 미기재", True),
        "non_numeric_preference": (-4, "장기근무 선호", True),
    }
    if duration_status in duration_adjustments:
        fit_adjust(*duration_adjustments[duration_status][:2], caution=duration_adjustments[duration_status][2])

    requirements = [str(value) for value in record.get("hard_requirements") or []]
    if not requirements:
        fit_adjust(3, "명시된 하드 요건 없음")
    for requirement in requirements:
        normalized = requirement.lower()
        if "health certificate" in normalized or "보건증" in requirement:
            fit_adjust(1, "보건증 요건 충족")
        elif "chinese" in normalized or "중국어" in requirement:
            fit_adjust(-12, "중국어 하드 요건 미충족", caution=True)
        elif "english" in normalized or "영어" in requirement:
            fit_adjust(2, "영어 요건 충족")
        elif "experience" in normalized or "경력" in requirement:
            fit_adjust(-6, "관련 실무 경력 필수", caution=True)
        elif "refrigerated/frozen" in normalized:
            fit_adjust(2, "여름 저온 물류 선호")
        elif "lift and handle stock" in normalized:
            fit_adjust(-2, "반복 중량물 작업", caution=True)
        elif any(marker in normalized for marker in ("178 cm", "outfit", "tattoo", "piercing")):
            fit_adjust(-4, f"하드 요건: {requirement}", caution=True)

    methods = set(record.get("application_methods") or [])
    if {"online", "easy_sms", "sms"} & methods:
        fit_adjust(1, "간편 지원 가능")
    elif "homepage" in methods:
        fit_adjust(-2, "외부 홈페이지 지원", caution=True)

    score = pay_points + commute_points + fit_points
    tier = "A" if score >= 90 else "B" if score >= 75 else "C" if score >= 60 else "D"
    metadata = {
        "comparable_hourly_pay": hourly,
        "work_hourly_pay": hourly if pay.get("reliable") else None,
        "pay_comparison_reliable": bool(pay.get("reliable")),
        "pay_comparison_basis": pay["basis"],
        "estimated_shift_earnings_won": pay.get("shift_earnings_won"),
        "paid_shift_hours": pay.get("paid_shift_hours"),
        "shift_span_hours": pay.get("shift_span_hours"),
        "door_to_door_hourly_pay": total_time_pay["hourly_equivalent"],
        "total_time_commitment_minutes": total_time_pay["total_minutes"],
        "door_to_door_pay_basis": total_time_pay["basis"],
        "pay_priority_score": pay_points,
        "commute_priority_score": commute_points,
        "fit_priority_score": fit_points,
    }
    return score, tier, reasons, cautions, metadata


def rerank_records(records: list[dict[str, Any]]) -> None:
    """Sort reliable work-hour pay first, using commute as the first tie-break."""

    for record in records:
        record["source_utility_rank"] = record.get("utility_rank")
        record["source_utility_score"] = record.get("utility_score")
        score, tier, reasons, cautions, metadata = assess_application_priority(record)
        record.update(metadata)
        record["utility_score"] = score
        record["utility_tier"] = tier
        record["utility_reasons"] = reasons
        record["utility_cautions"] = cautions
    records.sort(
        key=lambda item: (
            item.get("pay_comparison_reliable") is not True,
            -item["comparable_hourly_pay"],
            item["transit_minutes"]
            if isinstance(item.get("transit_minutes"), int)
            else 999,
            -item["utility_score"],
            -item["recruit_no"],
        ),
    )
    for rank, record in enumerate(records, start=1):
        record["utility_rank"] = rank


def application_message_fingerprint(job: dict[str, Any]) -> str:
    fields = (
        "recruit_no",
        "title",
        "company",
        "parts",
        "preferences",
        "skills",
        "hard_requirements",
        "certificate_requirements",
        "beginner_available",
        "work_days",
        "work_time",
        "work_time_option",
        "eligibility_lanes",
        "remote_job",
        "target_date_options",
        "late_night_shift_options",
        "late_night_availability",
        "availability_end_date",
        "duration_requirement",
        "application_methods",
        "pre_application_questions",
        "logistics_category",
        "cold_storage_status",
    )
    payload = {
        "ruleset": MESSAGE_RULESET_VERSION,
        "job": {field: job.get(field) for field in fields},
    }
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def load_message_cache(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {"version": MESSAGE_RULESET_VERSION, "jobs": {}}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"version": MESSAGE_RULESET_VERSION, "jobs": {}}
    if payload.get("version") != MESSAGE_RULESET_VERSION or not isinstance(
        payload.get("jobs"), dict
    ):
        return {"version": MESSAGE_RULESET_VERSION, "jobs": {}}
    return payload


def write_message_cache(path: Path, records: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fields = sorted(MESSAGE_CACHE_FIELDS)
    payload = {
        "version": MESSAGE_RULESET_VERSION,
        "updated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "job_count": len(records),
        "jobs": {
            str(record["recruit_no"]): {
                "fingerprint": record["message_fingerprint"],
                "content": {field: record[field] for field in fields},
            }
            for record in records
        },
    }
    atomic_write_text(
        path,
        json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + "\n",
    )


def build_records(
    report: dict[str, Any],
    dispositions: dict[int, dict[str, str]] | None = None,
    transit_cache: dict[int, dict[str, Any]] | None = None,
    message_cache: dict[str, Any] | None = None,
    message_cache_metrics: dict[str, int] | None = None,
) -> list[dict[str, Any]]:
    """Join the latest job metadata to its tailored message."""

    dispositions = dispositions or {}
    transit_cache = transit_cache or {}
    cached_jobs = (message_cache or {}).get("jobs") or {}
    cache_hits = 0
    cache_misses = 0
    regular_jobs = report.get("jobs") or []
    logistics_jobs = report.get("logistics_jobs") or []
    records: list[dict[str, Any]] = []
    for job in [*regular_jobs, *logistics_jobs]:
        recruit_no = int(job["recruit_no"])
        message_fingerprint = application_message_fingerprint(job)
        cached_message = cached_jobs.get(str(recruit_no))
        cached_content = (
            cached_message.get("content")
            if isinstance(cached_message, dict)
            and cached_message.get("fingerprint") == message_fingerprint
            else None
        )
        if not isinstance(cached_content, dict) or not MESSAGE_CACHE_FIELDS.issubset(
            cached_content
        ):
            cached_content = None
        transit = transit_cache.get(recruit_no) or {}
        pay = comparable_hourly_pay(job)
        commute_policy = commute_policy_for_job(
            job,
            transit,
            hourly_pay=int(pay["hourly_equivalent"]),
        )
        distance_excluded = bool(commute_policy["distance_excluded"])
        if cached_content is not None:
            cache_hits += 1
            message_fields = cached_content
            role_family = str(message_fields["role_family"])
            profile_mismatch = known_profile_mismatch(job, role_family)
        else:
            cache_misses += 1
            if job.get("report_section") == "logistics":
                if "B-Mart" in str(job.get("logistics_category")):
                    item = bmart_spec(job)
                    message_origin = "logistics_template"
                elif job.get("is_coupang") or "쿠팡" in " ".join(
                    str(job.get(field) or "")
                    for field in ("company", "title", "logistics_category")
                ):
                    item = coupang_spec(job)
                    message_origin = "logistics_template"
                else:
                    item = fallback_regular_spec(job)
                    message_origin = "relevance_grounded"
            else:
                item = REGULAR_SPECS.get(recruit_no) or fallback_regular_spec(job)
                message_origin = (
                    "curated"
                    if recruit_no in REGULAR_SPECS
                    else "relevance_grounded"
                )
            role_family = item.get("role_family") or classify_role(job)
            priorities = item.get("employer_priorities") or employer_priorities(
                job, role_family
            )
            relevance = item.get("relevance_rationale") or "; ".join(
                FACT_RELEVANCE[key]
                for key in item["facts"]
                if key in FACT_RELEVANCE
            )
            profile_mismatch = known_profile_mismatch(job, role_family)
            if profile_mismatch and not item["fit_warning"]:
                item = {**item, "fit_warning": profile_mismatch}
            message_fields = {
                "message": compose_message(item),
                "message_origin": message_origin,
                "role_family": role_family,
                "employer_priorities": priorities,
                "relevance_rationale": relevance,
                "profile_facts_used": [
                    PROFILE_FACTS[key]["fact"] for key in item["facts"]
                ],
                "verify_before_sending": item["verify"],
                "optional_sentence_if_true": item["optional_sentence"],
                "fit_warning": item["fit_warning"],
            }
        message_fields = {
            **message_fields,
            "verify_before_sending": list(
                message_fields.get("verify_before_sending") or []
            ),
        }
        availability_end_date = str(job.get("availability_end_date") or "")
        if availability_end_date:
            availability_check = (
                f"필수 근무가 {availability_end_date}까지 종료되는지 최종 확인"
            )
            if availability_check not in message_fields["verify_before_sending"]:
                message_fields["verify_before_sending"].append(availability_check)
        is_remote = bool(
            job.get("remote_job")
            or "remote_short_term" in (job.get("eligibility_lanes") or [])
        )
        if is_remote:
            for remote_check in (
                "시급 적용 업무 범위·실근로시간·지급일 확인",
                "선입금·회원가입·계정공유·과도한 사진/영상 제출 요구가 없는지 확인",
            ):
                if remote_check not in message_fields["verify_before_sending"]:
                    message_fields["verify_before_sending"].append(remote_check)
        is_late_night = "songpa_late_night" in (job.get("eligibility_lanes") or [])
        late_night_journeys = (
            transit.get("late_night_shift_journeys") or []
            if is_late_night
            else []
        )
        if is_late_night:
            has_future_time_route = any(
                leg.get("mode") == "public_transit_future_time"
                for journey in late_night_journeys
                for leg in (
                    journey.get("outbound") or {},
                    journey.get("return") or {},
                )
            )
            night_transport_check = (
                "Naver 미래시간 경로를 실제 근무일에 재확인하고 자전거 귀가 가능 여부 확인"
                if has_future_time_route
                else (
                    "근무 당일 심야 버스 운행과 자전거 귀가 교통수단 가능 여부"
                    if late_night_journeys
                    else "실제 퇴근 시간의 귀가 교통수단"
                )
            )
            if night_transport_check not in message_fields["verify_before_sending"]:
                message_fields["verify_before_sending"].append(
                    night_transport_check
                )
        if transit.get("route_cache_stale"):
            stale_route_check = "오래된 Kakao 통근 경로를 출발 전 재확인"
            if stale_route_check not in message_fields["verify_before_sending"]:
                message_fields["verify_before_sending"].append(stale_route_check)
        user_disposition = dispositions.get(recruit_no) or {}
        application_disposition = (
            "applied"
            if job.get("already_applied")
            else (
                "excluded_by_distance"
                if distance_excluded
                else str(
                    user_disposition.get("status")
                    or ("maybe_later" if profile_mismatch else "apply")
                )
            )
        )
        records.append(
            {
                "report_section": job["report_section"],
                "already_applied": bool(job.get("already_applied")),
                "application_disposition": application_disposition,
                "disposition_reason": str(
                    user_disposition.get("reason") or profile_mismatch
                ),
                "distance_excluded": distance_excluded,
                "transit_route_status": str(transit.get("route_status") or ""),
                "transit_minutes": transit.get("transit_minutes"),
                "transit_type": str(transit.get("transit_type") or ""),
                "transit_transfers": transit.get("transfers"),
                "transit_vehicles": transit.get("vehicles") or [],
                "transit_route_url": str(transit.get("landing_url") or ""),
                "transit_route_stale": bool(transit.get("route_cache_stale")),
                "late_night_route_checked_at": str(
                    transit.get("late_night_route_checked_at") or ""
                ),
                "late_night_route_time_policy": str(
                    transit.get("late_night_route_time_policy") or ""
                ),
                "late_night_shift_journeys": late_night_journeys,
                "return_bicycle_minutes": transit.get("return_bicycle_minutes"),
                "return_bicycle_distance_km": transit.get(
                    "return_bicycle_distance_km"
                ),
                "distance_exclusion_reason": commute_policy[
                    "distance_exclusion_reason"
                ],
                **{
                    key: commute_policy[key]
                    for key in (
                        "search_zone",
                        "search_zone_label",
                        "commute_limit_minutes",
                        "commute_extension_minutes",
                        "commute_policy_reason",
                        "commute_policy_eligible",
                        "pay_adjusted_commute",
                        "evening_station_area_status",
                        "evening_station_area_eligible",
                        "evening_station_area_limit_minutes",
                        "evening_nearest_station",
                        "evening_station_distance_km",
                        "evening_station_walk_minutes",
                        "evening_station_area_basis",
                    )
                },
                "applied_at": job.get("applied_at") or "",
                "applied_method": job.get("applied_method") or "",
                "application_read_status": job.get("application_read_status") or "",
                "utility_rank": job["utility_rank"],
                "utility_tier": job["utility_tier"],
                "utility_score": job["utility_score"],
                "utility_reasons": job.get("utility_reasons") or [],
                "utility_cautions": job.get("utility_cautions") or [],
                "recruit_no": recruit_no,
                "company": job["company"],
                "title": job["title"],
                "url": job["url"],
                "area": job["area"],
                "work_days": job["work_days"],
                "work_time": job["work_time"],
                "work_time_option": job.get("work_time_option") or "",
                "weekend_shift_options": job.get("weekend_shift_options") or [],
                "eligibility_lanes": job.get("eligibility_lanes") or [],
                "remote_job": is_remote,
                "target_date_options": job.get("target_date_options") or [],
                "target_date_option_evidence": job.get("target_date_option_evidence") or [],
                "late_night_shift_options": job.get("late_night_shift_options") or [],
                "late_night_availability": job.get("late_night_availability") or "",
                "salary_type": job["salary_type"],
                "salary": job["salary"],
                "salary_notes": job.get("salary_notes") or [],
                "duration_requirement": job.get("duration_requirement") or "",
                "duration_review_status": job.get("duration_review_status") or "",
                "work_term_start": job.get("work_term_start") or "",
                "work_term_end": job.get("work_term_end") or "",
                "minimum_commitment_days": job.get("minimum_commitment_days"),
                "availability_window_start_date": job.get("availability_window_start_date") or "",
                "availability_end_date": availability_end_date,
                "availability_window_days": job.get("availability_window_days"),
                "availability_compatibility_basis": job.get("availability_compatibility_basis") or "",
                "weekend_compatibility_basis": job.get("weekend_compatibility_basis") or "",
                "logistics_category": job.get("logistics_category") or "",
                "application_methods": job["application_methods"],
                "hard_requirements": report_hard_requirements(job),
                "certificate_requirements": job.get("certificate_requirements") or [],
                "message_fingerprint": message_fingerprint,
                **message_fields,
                "send_readiness": (
                    "do_not_send_already_applied"
                    if application_disposition == "applied"
                    else "excluded_by_distance"
                    if application_disposition == "excluded_by_distance"
                    else "deferred_by_user"
                    if application_disposition in {"maybe_later", "do_not_apply"}
                    else (
                        "conditional_on_requirement"
                        if message_fields["fit_warning"]
                        else "ready_after_schedule_check"
                    )
                ),
            }
        )
    rerank_records(records)
    if message_cache_metrics is not None:
        message_cache_metrics.update(
            {"message_cache_hits": cache_hits, "message_cache_misses": cache_misses}
        )
    return records


def write_json(
    path: Path,
    source: Path,
    records: list[dict[str, Any]],
    *,
    source_report: dict[str, Any] | None = None,
) -> dict[str, Any]:
    payload = {
        "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "source_report": str(source),
        **(
            {"source_run_id": str(source_report["run_id"])}
            if source_report and source_report.get("run_id")
            else {}
        ),
        **(
            {"source_generated_at": str(source_report["generated_at"])}
            if source_report and source_report.get("generated_at")
            else {}
        ),
        "origin": TRANSIT_ORIGIN,
        "privacy_note": (
            "Only job-relevant facts are retained; contact details, exact address, "
            "birth date, and resume identifiers are excluded."
        ),
        "usage_note": (
            "Do not paste optional_sentence_if_true unless every statement in it is true. "
            "Recheck verify_before_sending immediately before applying."
        ),
        "profile_facts": PROFILE_FACTS,
        "ranking_model": PAY_PRIORITY_MODEL,
        "job_count": len(records),
        "not_yet_applied_count": sum(
            1 for record in records if not record["already_applied"]
        ),
        "actionable_count": sum(
            1 for record in records if record["application_disposition"] == "apply"
        ),
        "maybe_later_count": sum(
            1
            for record in records
            if record["application_disposition"] in {"maybe_later", "do_not_apply"}
        ),
        "distance_excluded_count": sum(
            1
            for record in records
            if record["application_disposition"] == "excluded_by_distance"
        ),
        "already_applied_count": sum(
            1 for record in records if record["already_applied"]
        ),
        "jobs": records,
    }
    atomic_write_text(path, json.dumps(payload, ensure_ascii=False, indent=2) + "\n")
    return payload


def render_late_night_journeys(record: dict[str, Any]) -> list[str]:
    """Render exact shift-time commute choices and their verification basis."""

    def short_time(value: Any) -> str:
        text = str(value or "")
        return text[5:16].replace("T", " ") if len(text) >= 16 else text

    rendered: list[str] = []
    for journey in record.get("late_night_shift_journeys") or []:
        outbound = journey.get("outbound") or {}
        return_route = journey.get("return") or {}
        bicycle = journey.get("bicycle_fallback") or {}
        mode = return_route.get("mode")
        if mode == "public_transit_future_time":
            vehicles = ", ".join(return_route.get("vehicles") or ["대중교통"])
            return_text = (
                f"귀가 Naver 미래시간 {return_route.get('minutes')}분/{vehicles} "
                f"({short_time(return_route.get('departure_at'))}→"
                f"{short_time(return_route.get('arrival_at'))}, 실제 근무일 재확인)"
            )
        elif mode == "night_bus_pattern":
            vehicles = ", ".join(return_route.get("vehicles") or ["심야 버스"])
            return_text = (
                f"귀가 {vehicles} 경로 {return_route.get('minutes')}분"
                "(당일 운행 확인)"
            )
        elif mode == "public_transit_pattern":
            return_text = (
                f"귀가 대중교통 경로 {return_route.get('minutes')}분"
                "(당일 운행 확인)"
            )
        else:
            return_text = (
                f"귀가 자전거 도로우선 {return_route.get('minutes')}분/"
                f"{return_route.get('distance_km')}km"
            )
        bike_text = (
            f"자전거 예비 {bicycle.get('minutes')}분/"
            f"{bicycle.get('distance_km')}km"
        )
        if outbound.get("mode") == "public_transit_future_time":
            outbound_text = (
                f"출근 Naver 미래시간 {outbound.get('minutes')}분 "
                f"({short_time(outbound.get('departure_at'))}→"
                f"{short_time(outbound.get('arrival_at'))}, 실제 근무일 재확인)"
            )
        else:
            outbound_text = (
                f"출근 대중교통 경로 {outbound.get('minutes')}분"
                "(당일 운행 확인)"
            )
        rendered.append(
            f"{journey.get('shift')}: {outbound_text} · {return_text} · {bike_text}"
        )
    return rendered


def write_csv(path: Path, records: list[dict[str, Any]]) -> None:
    fields = [
        "report_section",
        "already_applied",
        "application_disposition",
        "disposition_reason",
        "distance_excluded",
        "transit_route_status",
        "transit_minutes",
        "transit_type",
        "transit_transfers",
        "transit_vehicles",
        "transit_route_url",
        "transit_route_stale",
        "distance_exclusion_reason",
        "search_zone",
        "search_zone_label",
        "commute_limit_minutes",
        "commute_extension_minutes",
        "commute_policy_reason",
        "commute_policy_eligible",
        "pay_adjusted_commute",
        "evening_station_area_status",
        "evening_station_area_eligible",
        "evening_station_area_limit_minutes",
        "evening_nearest_station",
        "evening_station_distance_km",
        "evening_station_walk_minutes",
        "evening_station_area_basis",
        "applied_at",
        "applied_method",
        "application_read_status",
        "utility_rank",
        "utility_tier",
        "utility_score",
        "comparable_hourly_pay",
        "work_hourly_pay",
        "pay_comparison_reliable",
        "pay_comparison_basis",
        "estimated_shift_earnings_won",
        "paid_shift_hours",
        "shift_span_hours",
        "door_to_door_hourly_pay",
        "total_time_commitment_minutes",
        "door_to_door_pay_basis",
        "pay_priority_score",
        "commute_priority_score",
        "fit_priority_score",
        "utility_reasons",
        "utility_cautions",
        "recruit_no",
        "company",
        "title",
        "url",
        "area",
        "work_days",
        "work_time",
        "work_time_option",
        "weekend_shift_options",
        "eligibility_lanes",
        "target_date_options",
        "target_date_option_evidence",
        "late_night_shift_options",
        "late_night_availability",
        "late_night_route_checked_at",
        "late_night_route_time_policy",
        "late_night_shift_journeys",
        "return_bicycle_minutes",
        "return_bicycle_distance_km",
        "salary_type",
        "salary",
        "salary_notes",
        "duration_requirement",
        "work_term_start",
        "work_term_end",
        "minimum_commitment_days",
        "availability_window_start_date",
        "availability_end_date",
        "availability_window_days",
        "availability_compatibility_basis",
        "hard_requirements",
        "certificate_requirements",
        "application_methods",
        "message",
        "message_origin",
        "role_family",
        "employer_priorities",
        "relevance_rationale",
        "optional_sentence_if_true",
        "verify_before_sending",
        "fit_warning",
        "send_readiness",
    ]
    with atomic_text_writer(
        path, newline="", encoding="utf-8-sig"
    ) as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for record in records:
            row = {key: record.get(key, "") for key in fields}
            row["verify_before_sending"] = " | ".join(record["verify_before_sending"])
            for key in (
                "utility_reasons",
                "utility_cautions",
                "hard_requirements",
                "certificate_requirements",
                "application_methods",
                "transit_vehicles",
                "employer_priorities",
                "salary_notes",
                "late_night_shift_journeys",
            ):
                row[key] = " | ".join(str(value) for value in record[key])
            writer.writerow(row)


def write_markdown(path: Path, records: list[dict[str, Any]]) -> None:
    lines = [
        "# 알바몬 통합 지원 보고서",
        "",
        f"> 환산 시급이 높은 순으로 먼저 정렬하고, 같은 급여에서는 {TRANSIT_ORIGIN['name']} 대중교통 시간이 짧은 순으로 정렬했습니다. 기본 기준은 50분 이하이며 용산·영등포만 확정 급여에 따라 최대 90분까지 연장합니다. `보내기 전 확인`을 확인한 뒤 메시지를 사용하세요.",
        "",
        "## 사용한 지원자 강점",
        "",
    ]
    for value in PROFILE_FACTS.values():
        lines.append(f"- {value['fact']} ({value['source']})")
    lines.extend(["", "개인 연락처·정확한 주소·생년월일·이력서 번호는 저장하지 않았습니다.", ""])

    sections = (
        ("jobs", "apply", "지원 전 — 일반 일자리"),
        ("logistics", "apply", "지원 전 — 물류 일자리"),
        (None, "maybe_later", "부적합·나중에 지원 검토"),
        (None, "excluded_by_distance", "거리 제외 — 적용 통근 기준 초과 또는 경로 미검증"),
        (None, "applied", "이미 지원함 — 재지원하지 말 것"),
    )
    for section, disposition, heading in sections:
        lines.extend([f"## {heading}", ""])
        section_records = [
            item
            for item in records
            if (
                item["application_disposition"] == disposition
                or (
                    disposition == "maybe_later"
                    and item["application_disposition"] == "do_not_apply"
                )
            )
            and (section is None or item["report_section"] == section)
        ]
        if not section_records:
            lines.extend(["- 없음", ""])
            continue
        if disposition == "applied":
            lines.extend(
                [
                    "> 아래 메시지는 참고용으로 보존합니다. 지원 이력과 일치하므로 다시 제출하지 마세요.",
                    "",
                ]
            )
        elif disposition == "maybe_later":
            lines.extend(
                [
                    "> 현재 적합도가 낮거나 조건 확인이 더 필요한 공고입니다. 자동 지원 대상에서 제외합니다.",
                    "",
                ]
            )
        elif disposition == "excluded_by_distance":
            lines.extend(
                [
                    "> 버스·지하철 최단 경로가 해당 공고의 통근 허용 기준을 넘거나 실제 근무지까지의 경로를 검증할 수 없는 공고입니다. 지원 대상에서 제외합니다.",
                    "",
                ]
            )
        for section_rank, record in enumerate(section_records, start=1):
            work_hourly = (
                f"근무 환산시급 약 {record['work_hourly_pay']:,}원"
                if isinstance(record.get("work_hourly_pay"), int)
                else "근무 환산시급 확인 필요"
            )
            total_time_hourly = (
                f"총투입시간 시급 약 {record['door_to_door_hourly_pay']:,}원"
                if isinstance(record.get("door_to_door_hourly_pay"), int)
                else "총투입시간 시급 계산 불가"
            )
            lines.extend(
                [
                    f"### {section_rank}. [{record['company']} — {record['title']}]({record['url']}) — {record['salary_type']} {record['salary']} · {work_hourly} · {total_time_hourly} · {record['utility_tier']} / {record['utility_score']}점 (전체 #{record['utility_rank']})",
                    "",
                    "**순위 근거**",
                    "",
                    "- 가점: " + "; ".join(record["utility_reasons"] or ["하드 필터 통과"]),
                    "- 감점·주의: " + "; ".join(record["utility_cautions"] or ["확인된 감점 없음"]),
                    "",
                    "**핵심 조건**",
                    "",
                    f"- 검색 구역: {record['search_zone_label']}",
                    f"- 지역: {record['area']}",
                    "- 적격 검색 경로: "
                    + ", ".join(record.get("eligibility_lanes") or ["weekend_short_term"]),
                    f"- 일정: {record['work_days']}; {record['work_time']}",
                    "- 상세 주말 슬롯: "
                    + "; ".join(record.get("weekend_shift_options") or ["별도 슬롯 없음"]),
                    "- 8/15·8/17 1일 선택지: "
                    + "; ".join(record.get("target_date_options") or ["해당 없음"]),
                    "- 19:00~02:00 내 야간 슬롯: "
                    + "; ".join(record.get("late_night_shift_options") or ["해당 없음"]),
                    f"- 급여: {record['salary_type']} {record['salary']}",
                    "- 급여 주의: "
                    + "; ".join(record.get("salary_notes") or ["추가 조건 없음"]),
                    f"- 근무 환산시급: {work_hourly} — {record['pay_comparison_basis']}",
                    f"- 총투입시간 시급: {total_time_hourly} — {record.get('door_to_door_pay_basis') or '계산 근거 없음'}",
                    f"- 근무기간: {record['duration_requirement'] or '숫자 최소기간 미확인'}",
                    f"- 근무 가능 종료일: {record.get('availability_end_date') or '미설정'}",
                    f"- 종료일 적합성: {record.get('availability_compatibility_basis') or '지원 전 확인 필요'}",
                    "- 하드 요건: " + "; ".join(record["hard_requirements"] or ["명시된 요건 없음"]),
                    "- 지원방법: " + ", ".join(record["application_methods"]),
                    f"- {TRANSIT_ORIGIN['name']} 대중교통: "
                    + (
                        f"{record['transit_minutes']}분"
                        if record["transit_minutes"] is not None
                        else "실제 근무지 기준 검증 불가"
                    )
                    + (
                        f" ({', '.join(record['transit_vehicles'])})"
                        if record["transit_vehicles"]
                        else ""
                    ),
                    f"- 통근 허용 기준: {record['commute_policy_reason']}",
                    "- 시간대별 야간 통근: "
                    + "; ".join(
                        render_late_night_journeys(record) or ["해당 없음"]
                    ),
                    "",
                ]
            )
            if disposition == "excluded_by_distance":
                lines.extend(
                    [
                        "**거리 제외 이유**: "
                        + (record["distance_exclusion_reason"] or "적용 통근 기준 미충족"),
                        "",
                    ]
                )
            else:
                lines.extend(
                    [
                        "**메시지 초점**: "
                        + " · ".join(record["employer_priorities"]),
                        "",
                        "**경력 연결 근거**: " + record["relevance_rationale"],
                        "",
                        "**지원 메시지**",
                        "",
                        record["message"],
                        "",
                        "**사용한 근거**: " + ", ".join(record["profile_facts_used"]),
                        "",
                        "**보내기 전 확인**: " + "; ".join(record["verify_before_sending"]),
                        "",
                    ]
                )
            if disposition == "applied":
                status = " · ".join(
                    value
                    for value in (
                        record["applied_at"] or "날짜 미확인",
                        record["applied_method"] or "지원 방법 미확인",
                        record["application_read_status"] or "열람 상태 미확인",
                    )
                    if value
                )
                lines.extend(["**지원 이력**: " + status, ""])
            elif disposition == "maybe_later":
                lines.extend(
                    [
                        "**보류 이유**: "
                        + (record["disposition_reason"] or "사용자 지정 보류"),
                        "",
                    ]
                )
            if record["optional_sentence_if_true"]:
                lines.extend(
                    [
                        "**사실일 때만 추가**: " + record["optional_sentence_if_true"],
                        "",
                    ]
                )
            if record["fit_warning"]:
                lines.extend(["**적합성 경고**: " + record["fit_warning"], ""])
    atomic_write_text(path, "\n".join(lines).rstrip() + "\n")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, default=DEFAULT_INPUT)
    parser.add_argument("--output-prefix", type=Path, default=DEFAULT_OUTPUT_PREFIX)
    parser.add_argument("--dispositions", type=Path, default=DEFAULT_DISPOSITIONS)
    parser.add_argument("--transit-cache", type=Path, default=DEFAULT_TRANSIT_CACHE)
    parser.add_argument(
        "--message-cache", type=Path, default=DEFAULT_MESSAGE_CACHE
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    report = json.loads(args.input.read_text(encoding="utf-8"))
    message_metrics: dict[str, int] = {}
    records = build_records(
        report,
        load_dispositions(args.dispositions),
        load_transit_cache(args.transit_cache),
        load_message_cache(args.message_cache),
        message_metrics,
    )
    args.output_prefix.parent.mkdir(parents=True, exist_ok=True)
    write_json(
        args.output_prefix.with_suffix(".json"),
        args.input,
        records,
        source_report=report,
    )
    write_csv(args.output_prefix.with_suffix(".csv"), records)
    write_markdown(args.output_prefix.with_suffix(".md"), records)
    write_message_cache(args.message_cache, records)
    print(
        f"Wrote {len(records)} ranked applications to {args.output_prefix}.*; "
        f"reused {message_metrics['message_cache_hits']} message analyses and "
        f"generated {message_metrics['message_cache_misses']}."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
