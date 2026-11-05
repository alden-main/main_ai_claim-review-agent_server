"""확인된 필드 매핑으로 원천 레코드를 변환하고 오류를 분리한다."""

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Any


@dataclass(frozen=True)
class FieldMapping:
    """논리 필드의 원천 이름, 자료형 및 필수 여부를 나타낸다."""

    source: str
    kind: str = "text"
    required: bool = False


def normalize_record(record: dict[str, Any], mappings: dict[str, FieldMapping], *, artifact_id: str, location: str, mapping_version: str) -> dict:
    """명시된 매핑만 적용하고 원본 위치와 필드별 결측·오류를 보존한다.

    Args:
        record: 원천 필드명과 값을 담은 레코드.
        mappings: 논리 필드별로 확인된 원천 필드와 변환 규칙.
        artifact_id: 원본 내용 해시 등 원본 식별자.
        location: 원본 내 행 또는 레코드 위치.
        mapping_version: 적용한 매핑 규칙의 버전.
    Returns:
        원본 값, 변환 값, 필드 상태 및 격리 사유가 포함된 결과.
    """
    if not artifact_id or not location or not mapping_version:
        raise ValueError("원본 식별자, 위치와 매핑 버전이 필요합니다")
    fields = {}
    issues = []
    for name, mapping in mappings.items():
        if mapping.kind not in {"text", "decimal"}:
            raise ValueError(f"지원하지 않는 변환 유형: {mapping.kind}")
        raw = record.get(mapping.source)
        if mapping.source not in record:
            fields[name] = {"value": None, "raw": None, "status": "unverified"}
        elif raw is None or raw == "":
            fields[name] = {"value": None, "raw": raw, "status": "not_provided"}
        else:
            try:
                if mapping.kind == "text":
                    if not isinstance(raw, str):
                        raise ValueError("문자열 원본이 아니므로 안전하게 변환할 수 없습니다")
                    value = raw.strip()
                    if not value:
                        raise ValueError("공백만 있는 값입니다")
                else:
                    if isinstance(raw, (float, bool)):
                        raise ValueError("원본 숫자 문자열이 필요합니다")
                    number = Decimal(str(raw))
                    if not number.is_finite():
                        raise ValueError("유한한 숫자가 아닙니다")
                    value = str(number)
                fields[name] = {"value": value, "raw": raw, "status": "provided"}
            except (ValueError, InvalidOperation) as exc:
                fields[name] = {"value": None, "raw": raw, "status": "invalid"}
                issues.append({"field": name, "reason": str(exc)})
        if mapping.required and fields[name]["status"] in {"unverified", "not_provided"}:
            issues.append({"field": name, "reason": "필수 필드가 없거나 확인되지 않았습니다"})
    return {"artifact_id": artifact_id, "location": location, "mapping_version": mapping_version,
            "fields": fields, "quality_status": "failed" if issues else "pending", "issues": issues}
