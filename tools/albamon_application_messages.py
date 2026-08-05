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
import re
from datetime import datetime
from pathlib import Path
from typing import Any


DEFAULT_INPUT = Path(
    "artifacts/albamon/2026-08-01-combined-matched-summer-urgent.json"
)
DEFAULT_OUTPUT_PREFIX = Path("artifacts/albamon/2026-08-01-application-messages")
DEFAULT_DISPOSITIONS = Path("tools/albamon_application_dispositions.json")
DEFAULT_TRANSIT_CACHE = Path("tools/albamon_transit_cache.json")
DEFAULT_MESSAGE_CACHE = Path("artifacts/albamon/cache/application_messages.json")
TRANSIT_CUTOFF_MINUTES = 50
MESSAGE_RULESET_VERSION = 1
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
    "sort_mode": "strict lexicographic: comparable pay descending, then transit minutes ascending",
    "ordering": [
        "comparable hourly compensation",
        "public-transit minutes from Jamsilsaenae Station",
        "schedule certainty, duration, requirements, and application friction",
    ],
    "pay_points": "0-80; one point per KRW 100 above KRW 10,320/hour",
    "commute_points": "0-20; scaled across routes displayed below the strict 50-minute cutoff",
    "tier_thresholds": {"A": 90, "B": 75, "C": 60, "D": 0},
}

# These two headlines are not comparable at their structured salary value. The
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
    duties = [
        str(value).strip()
        for value in job.get("skills") or []
        if str(value).strip() and str(value).strip() not in SOFT_SKILLS
    ]
    if duties:
        return duties[:4]
    title = str(job.get("title") or "")
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


def comparable_shift_hours(record: dict[str, Any]) -> tuple[float | None, str]:
    """Return conservative paid hours for normalizing a daily/monthly amount."""

    title = str(record.get("title") or "")
    option = str(record.get("work_time_option") or "")
    work_time = str(record.get("work_time") or "")
    explicit = re.search(r"실근로\s*(\d+(?:\.\d+)?)\s*시간", title)
    if explicit:
        return float(explicit.group(1)), "제목에 명시된 실근로시간"
    range_hours = re.search(r"(\d+(?:\.\d+)?)\s*[~～-]\s*(\d+(?:\.\d+)?)\s*시간", option)
    if range_hours:
        return float(range_hours.group(2)), "근무시간 범위의 최대치로 보수적 환산"
    times = re.search(r"(\d{1,2}):(\d{2})\s*[~～-]\s*(\d{1,2}):(\d{2})", work_time)
    if not times:
        return None, "근무시간 미확인"
    start = int(times.group(1)) + int(times.group(2)) / 60
    end = int(times.group(3)) + int(times.group(4)) / 60
    if end <= start:
        end += 24
    hours = end - start
    break_match = re.search(r"휴게시간\s*(\d+)\s*분", option)
    if break_match:
        hours -= int(break_match.group(1)) / 60
    return max(hours, 0.5), "표시 근무시간에서 휴게시간을 제외"


def comparable_hourly_pay(record: dict[str, Any]) -> dict[str, Any]:
    """Normalize mixed salary units without treating headline maxima as guarantees."""

    recruit_no = int(record.get("recruit_no") or 0)
    override = PAY_COMPARISON_OVERRIDES.get(recruit_no)
    if override:
        return dict(override)
    amount = numeric_pay(record)
    salary_type = str(record.get("salary_type") or "")
    hours, hours_basis = comparable_shift_hours(record)
    confidence_penalty = 0
    if salary_type == "시급":
        equivalent = amount
        basis = "공고 시급"
    elif salary_type == "일급":
        if hours is None:
            hours = 8.0
            hours_basis = "근무시간 미확인으로 8시간 가정"
            confidence_penalty = -5
        equivalent = round(amount / hours) if hours else 0
        basis = f"공고 일급을 {hours:g}시간으로 환산 ({hours_basis})"
    elif salary_type == "월급":
        weekly_hours = re.search(r"주\s*(\d+(?:\.\d+)?)\s*시간", str(record.get("title") or ""))
        if weekly_hours:
            monthly_hours = float(weekly_hours.group(1)) * 52 / 12
            basis = f"공고의 주 {weekly_hours.group(1)}시간을 월환산"
        elif hours is not None and "주말" in str(record.get("work_days") or ""):
            monthly_hours = hours * 2 * 52 / 12
            basis = f"토·일 각 {hours:g}시간을 월환산 ({hours_basis})"
        else:
            monthly_hours = 209
            basis = "주간 근무시간 미확인으로 월 209시간 가정"
            confidence_penalty = -5
        equivalent = round(amount / monthly_hours) if monthly_hours else 0
    elif salary_type == "주급":
        equivalent = round(amount / 40) if amount else 0
        basis = "주간 근무시간 미확인으로 40시간 가정"
        confidence_penalty = -10
    else:
        equivalent = 0
        basis = "비교 가능한 급여 단위 미확인"
        confidence_penalty = -10
    return {
        "hourly_equivalent": max(0, equivalent),
        "basis": basis,
        "caution": "" if confidence_penalty == 0 else "환산에 필요한 근무시간이 완전히 확인되지 않음",
        "confidence_penalty": confidence_penalty,
    }


def assess_application_priority(
    record: dict[str, Any],
) -> tuple[int, str, list[str], list[str], dict[str, Any]]:
    """Score pay first, exact public-transit commute second, and fit third."""

    pay = comparable_hourly_pay(record)
    hourly = int(pay["hourly_equivalent"])
    pay_points = max(0, min(80, round((hourly - 10_320) / 100)))
    minutes = record.get("transit_minutes")
    commute_points = (
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
    reasons = [
        f"{pay_points:+d} 급여 우선점수: 환산 시급 약 {hourly:,}원 ({pay['basis']})",
        (
            f"{commute_points:+d} 통근 우선점수: 잠실새내역에서 대중교통 {minutes}분"
            if isinstance(minutes, int)
            else "+0 통근 우선점수: 실제 근무지 기준 미검증"
        ),
    ]
    cautions: list[str] = []
    if pay.get("caution"):
        cautions.append(str(pay["caution"]))

    def fit_adjust(points: int, label: str, *, caution: bool = False) -> None:
        nonlocal fit_points
        fit_points += points
        rendered = f"{points:+d} {label}"
        (cautions if caution else reasons).append(rendered)

    weekend_basis = str(record.get("weekend_compatibility_basis") or "")
    weekend_explicit = (
        weekend_basis.startswith("weekend") or weekend_basis == "explicit weekdays"
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
        "pay_comparison_basis": pay["basis"],
        "pay_priority_score": pay_points,
        "commute_priority_score": commute_points,
        "fit_priority_score": fit_points,
    }
    return score, tier, reasons, cautions, metadata


def rerank_records(records: list[dict[str, Any]]) -> None:
    """Sort strictly by comparable pay, using commute as the first tie-break."""

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
        "duration_requirement",
        "application_methods",
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
    temporary_path = path.with_suffix(path.suffix + ".tmp")
    temporary_path.write_text(
        json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + "\n",
        encoding="utf-8",
    )
    temporary_path.replace(path)


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
        distance_excluded = bool(transit.get("distance_excluded"))
        if cached_content is not None:
            cache_hits += 1
            message_fields = cached_content
            role_family = str(message_fields["role_family"])
            profile_mismatch = str(message_fields["fit_warning"])
        else:
            cache_misses += 1
            if job.get("report_section") == "logistics":
                item = (
                    bmart_spec(job)
                    if "B-Mart" in str(job.get("logistics_category"))
                    else coupang_spec(job)
                )
                message_origin = "logistics_template"
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
                "distance_exclusion_reason": str(
                    transit.get("distance_exclusion_reason") or ""
                ),
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
                "salary_type": job["salary_type"],
                "salary": job["salary"],
                "salary_notes": job.get("salary_notes") or [],
                "duration_requirement": job.get("duration_requirement") or "",
                "duration_review_status": job.get("duration_review_status") or "",
                "weekend_compatibility_basis": job.get("weekend_compatibility_basis") or "",
                "logistics_category": job.get("logistics_category") or "",
                "application_methods": job["application_methods"],
                "hard_requirements": job["hard_requirements"],
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


def write_json(path: Path, source: Path, records: list[dict[str, Any]]) -> None:
    payload = {
        "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "source_report": str(source),
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
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


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
        "distance_exclusion_reason",
        "applied_at",
        "applied_method",
        "application_read_status",
        "utility_rank",
        "utility_tier",
        "utility_score",
        "comparable_hourly_pay",
        "pay_comparison_basis",
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
        "salary_type",
        "salary",
        "salary_notes",
        "duration_requirement",
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
    with path.open("w", newline="", encoding="utf-8-sig") as handle:
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
            ):
                row[key] = " | ".join(str(value) for value in record[key])
            writer.writerow(row)


def write_markdown(path: Path, records: list[dict[str, Any]]) -> None:
    lines = [
        "# 알바몬 통합 지원 보고서",
        "",
        "> 환산 시급이 높은 순으로 먼저 정렬하고, 같은 급여에서는 잠실새내역 대중교통 시간이 짧은 순으로 정렬했습니다. `보내기 전 확인`을 확인한 뒤 메시지를 사용하세요.",
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
        (None, "excluded_by_distance", "거리 제외 — 잠실새내역 대중교통 50분 미만 기준"),
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
                    "> 버스·지하철 최단 경로가 50분 미만이 아니거나 실제 근무지까지의 경로를 검증할 수 없는 공고입니다. 지원 대상에서 제외합니다.",
                    "",
                ]
            )
        for section_rank, record in enumerate(section_records, start=1):
            lines.extend(
                [
                    f"### {section_rank}. [{record['company']} — {record['title']}]({record['url']}) — {record['salary_type']} {record['salary']} · 비교 시급 약 {record['comparable_hourly_pay']:,}원 · {record['utility_tier']} / {record['utility_score']}점 (전체 #{record['utility_rank']})",
                    "",
                    "**순위 근거**",
                    "",
                    "- 가점: " + "; ".join(record["utility_reasons"] or ["하드 필터 통과"]),
                    "- 감점·주의: " + "; ".join(record["utility_cautions"] or ["확인된 감점 없음"]),
                    "",
                    "**핵심 조건**",
                    "",
                    f"- 지역: {record['area']}",
                    f"- 일정: {record['work_days']}; {record['work_time']}",
                    f"- 급여: {record['salary_type']} {record['salary']}",
                    f"- 급여 비교 기준: 환산 시급 약 {record['comparable_hourly_pay']:,}원 — {record['pay_comparison_basis']}",
                    f"- 근무기간: {record['duration_requirement'] or '숫자 최소기간 미확인'}",
                    "- 하드 요건: " + "; ".join(record["hard_requirements"] or ["명시된 요건 없음"]),
                    "- 지원방법: " + ", ".join(record["application_methods"]),
                    "- 잠실새내역 대중교통: "
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
                    "",
                ]
            )
            if disposition == "excluded_by_distance":
                lines.extend(
                    [
                        "**거리 제외 이유**: "
                        + (record["distance_exclusion_reason"] or "50분 미만 기준 미충족"),
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
    path.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")


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
    write_json(args.output_prefix.with_suffix(".json"), args.input, records)
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
