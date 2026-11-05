"""국가법령정보 행정규칙 API를 조회하고 원본 응답을 검증한다."""

import os
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from pathlib import Path


class LawAPIError(Exception):
    """인증값을 노출하지 않고 법령 API 조회 실패를 나타낸다."""


def fetch_rule(rule_id: str, output: Path, *, timeout: float = 30) -> dict:
    """행정규칙 본문 XML을 조회하여 검증 후 원본 바이트를 새 파일로 저장한다.

    Args:
        rule_id: 조회할 행정규칙 일련번호.
        output: 덮어쓰지 않을 응답 저장 경로.
        timeout: 요청 대기 시간(초).
    Returns:
        응답의 확인 가능한 문서 메타데이터와 검토 상태.
    Raises:
        ValueError: 식별자 또는 환경변수가 올바르지 않은 경우.
        LawAPIError: 요청 또는 응답 검증에 실패한 경우.
        OSError: 파일 저장에 실패한 경우.
    """
    if not rule_id.isascii() or not rule_id.isdigit():
        raise ValueError("행정규칙 일련번호는 숫자여야 합니다")
    key = os.environ.get("LAW_API_OC", "").strip()
    if not key:
        raise ValueError("LAW_API_OC 환경변수가 필요합니다")
    params = urllib.parse.urlencode({"OC": key, "target": "admrul", "type": "XML", "ID": rule_id})
    request = urllib.request.Request("https://www.law.go.kr/DRF/lawService.do?" + params)
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            data = response.read()
    except (urllib.error.URLError, TimeoutError, OSError):
        raise LawAPIError("행정규칙 API 요청에 실패했습니다. 인증과 네트워크 상태를 확인하세요") from None
    metadata = inspect_rule_xml(data, expected_id=rule_id)
    with Path(output).open("xb") as target:
        target.write(data)
    return metadata


def inspect_rule_xml(data: bytes, *, expected_id: str | None = None) -> dict:
    """본문 응답의 식별자와 주요 메타데이터를 확인하고 미확인 상태를 반환한다.

    Args:
        data: API가 반환한 XML 원본 바이트.
        expected_id: 요청한 행정규칙 일련번호. 지정 시 응답과 대조한다.
    Returns:
        문서 메타데이터와 적용 범위·문서 관계의 확인 상태.
    Raises:
        LawAPIError: XML 오류, 오류 응답 또는 식별자 불일치가 발생한 경우.
    """
    try:
        root = ET.fromstring(data)
    except ET.ParseError:
        raise LawAPIError("XML 본문을 해석할 수 없습니다") from None
    def find_text(name: str) -> str | None:
        for node in root.iter():
            if node.tag.split("}")[-1] == name and node.text and node.text.strip():
                return node.text.strip()
        return None
    identifier = find_text("행정규칙일련번호")
    title = find_text("행정규칙명")
    if not identifier or not title or (expected_id is not None and identifier != expected_id):
        raise LawAPIError("행정규칙 본문 응답 또는 요청 일련번호를 확인할 수 없습니다")
    return {"rule_id": identifier, "title": title, "issuing_agency": find_text("소관부처명"),
            "notice_number": find_text("발령번호"), "issued_date_raw": find_text("발령일자"),
            "effective_date_raw": find_text("시행일자"), "scope_status": "unverified",
            "relationships_status": "unverified", "approval_status": "not_requested"}
