"""심평원 약국수가 API의 페이지별 원본 응답을 조회하고 확인한다."""

import os
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from pathlib import Path


PHARMACY_URL = "https://apis.data.go.kr/B551182/mdfeeCrtrInfoService/getPharmacyMdfeeList"


class FeeAPIError(Exception):
    """수가 API의 요청 또는 응답 검증 실패를 나타낸다."""


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """인증키가 포함된 요청을 다른 주소로 전달하지 않는다."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def inspect_fee_response(data: bytes) -> dict:
    """XML 응답의 결과 코드와 확인 가능한 페이지 정보를 검사한다.

    Args:
        data: 수가 API의 원본 XML 바이트.
    Returns:
        결과 코드와 페이지·건수 정보. 전체 원천 범위는 미확인이다.
    Raises:
        FeeAPIError: XML 또는 결과 코드가 없거나 API가 오류를 반환한 경우.
    """
    try:
        root = ET.fromstring(data)
    except ET.ParseError:
        raise FeeAPIError("응답이 올바른 XML이 아닙니다") from None
    elements = {node.tag.split("}")[-1]: (node.text or "").strip() for node in root.iter()}
    code = elements.get("resultCode") or elements.get("returnReasonCode")
    if code not in {"00", "000"}:
        # 오류 메시지에는 인증값이 포함될 수 있어 원문을 출력하지 않는다.
        safe_code = code if code and code.isascii() and code.isdigit() and len(code) <= 4 else "unknown"
        raise FeeAPIError(f"API 성공을 확인할 수 없습니다 (결과 코드: {safe_code})")
    if root.tag.split("}")[-1] != "response" or not any(node.tag.split("}")[-1] == "body" for node in root):
        raise FeeAPIError("API 응답 본문을 확인할 수 없습니다")
    items = sum(node.tag.split("}")[-1] == "item" for node in root.iter())
    return {"result_code": code, "page_no": elements.get("pageNo") or None,
            "page_size": elements.get("numOfRows") or elements.get("numOfRow") or None,
            "total_count": elements.get("totalCount") or None, "item_count": items,
            "completeness": "unverified"}


def fetch_pharmacy_page(output: Path, *, page: int = 1, page_size: int = 10,
                        code: str | None = None, category: str | None = None,
                        name: str | None = None, timeout: float = 30) -> dict:
    """약국수가 한 페이지의 XML을 조회하여 새 파일로 저장한다.

    Args:
        output: 덮어쓰지 않을 원본 응답 파일 경로.
        page: 요청 페이지 번호(1 이상).
        page_size: 페이지당 요청 건수(1~9999, 실제 허용 범위는 확인 필요).
        code: 선택적인 원천 수가코드.
        category: 선택적인 수가분류번호.
        name: 선택적인 수가 한글명 검색어.
        timeout: 요청 대기 시간(초).
    Returns:
        응답 확인 결과와 인증값을 제외한 요청 조건.
    Raises:
        ValueError: 요청 조건이나 환경변수가 유효하지 않은 경우.
        FeeAPIError: HTTP 또는 API 응답 오류가 발생한 경우.
        OSError: 원본 파일 저장에 실패한 경우.
    """
    if not 1 <= page <= 9999 or not 1 <= page_size <= 9999:
        raise ValueError("페이지 번호와 페이지당 건수는 1~9999여야 합니다")
    key = os.environ.get("HIRA_SERVICE_KEY", "").strip()
    if not key:
        raise ValueError("HIRA_SERVICE_KEY 환경변수가 필요합니다")
    filters = {k: v for k, v in (("mdfeeCd", code), ("mdfeeDivNo", category), ("korNm", name)) if v is not None}
    if any(not v.strip() or len(v) > limit for v, limit in ((code, 8), (category, 10), (name, 100)) if v is not None):
        raise ValueError("검색 조건이 비었거나 허용 길이를 초과했습니다")
    params = {"ServiceKey": key, "numOfRow": page_size, "pageNo": page, **filters}
    request = urllib.request.Request(PHARMACY_URL + "?" + urllib.parse.urlencode(params))
    try:
        with urllib.request.build_opener(_NoRedirect()).open(request, timeout=timeout) as response:
            data = response.read()
    except (urllib.error.URLError, TimeoutError, OSError):
        raise FeeAPIError("수가 API 요청에 실패했습니다. 인증·호출 제한과 네트워크 상태를 확인하세요") from None
    result = inspect_fee_response(data)
    with Path(output).open("xb") as target:
        target.write(data)
    result["request"] = {"pageNo": page, "numOfRow": page_size, **filters}
    return result
