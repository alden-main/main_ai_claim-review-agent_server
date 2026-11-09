"""수동 확보 파일의 수집과 실행 보고 명령을 제공한다."""

import argparse
import json
from pathlib import Path

from .fee_xlsx import MAPPING_VERSION, extract_workbook
from .fee_search import normalize_fees, search_fees
from .fee_api import FeeAPIError, fetch_pharmacy_page
from .law import LawAPIError, fetch_rule
from .postgres import PostgresStore


def main() -> None:
    """명령행 인자로 수집하거나 저장된 실행 보고서를 출력한다.

    Args:
        없음. 명령행 인자에서 저장소와 작업 대상을 읽는다.
    Returns:
        없음. 실행 결과를 표준 출력으로 기록한다.
    """
    parser = argparse.ArgumentParser(description="수동 확보 원본 수집")
    parser.add_argument("--store", type=Path, help="원본 및 카탈로그 저장 경로")
    commands = parser.add_subparsers(dest="command", required=True)
    collect = commands.add_parser("collect", help="파일 수집")
    collect.add_argument("--source", required=True)
    collect.add_argument("--scope", required=True)
    collect.add_argument("files", nargs="+", type=Path)
    report = commands.add_parser("report", help="실행 보고")
    report.add_argument("run_id")
    law = commands.add_parser("law-fetch", help="행정규칙 본문 XML 조회")
    law.add_argument("rule_id", help="행정규칙 일련번호")
    law.add_argument("output", type=Path, help="새 XML 파일 경로")
    fee = commands.add_parser("fee-pharmacy-fetch", help="약국수가 한 페이지 조회")
    fee.add_argument("output", type=Path, help="새 XML 파일 경로")
    fee.add_argument("--page", type=int, default=1)
    fee.add_argument("--page-size", type=int, default=10)
    fee.add_argument("--code")
    fee.add_argument("--category")
    fee.add_argument("--name")
    xlsx = commands.add_parser("fee-xlsx-extract", help="시범 수가 XLSX 추출")
    xlsx.add_argument("source", type=Path)
    xlsx.add_argument("output", type=Path, help="새 .jsonl.gz 파일 경로")
    load = commands.add_parser("fee-xlsx-load", help="수가 XLSX 추출 행을 PostgreSQL에 적재")
    load.add_argument("rows", type=Path)
    load.add_argument("report", type=Path)
    normal = commands.add_parser("fee-normalize", help="수가코드·명칭 검색용 정규화")
    normal.add_argument("artifact_sha256", help="원본 파일 SHA-256")
    normal.add_argument("--mapping-version", default=MAPPING_VERSION)
    search = commands.add_parser("fee-search", help="수가코드·명칭 검색")
    terms = search.add_mutually_exclusive_group(required=True)
    terms.add_argument("--code", help="정확히 일치할 수가코드")
    terms.add_argument("--name", help="명칭 포함 검색어")
    search.add_argument("--artifact", help="검색할 원본 SHA-256")
    search.add_argument("--limit", type=int, default=20)
    search.add_argument("--offset", type=int, default=0)
    args = parser.parse_args()
    if args.command == "fee-xlsx-extract":
        try:
            result = extract_workbook(args.source, args.output)
        except (ValueError, OSError) as exc:
            parser.exit(1, f"추출 실패: {exc}\n")
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return
    if args.command == "fee-pharmacy-fetch":
        try:
            result = fetch_pharmacy_page(args.output, page=args.page, page_size=args.page_size,
                                         code=args.code, category=args.category, name=args.name)
        except (FeeAPIError, ValueError, OSError) as exc:
            parser.exit(1, f"조회 실패: {exc}\n")
        print(json.dumps(result, ensure_ascii=False, indent=2))
        print("원본 등록은 collect 명령으로 수행하세요.")
        return
    if args.command == "law-fetch":
        try:
            metadata = fetch_rule(args.rule_id, args.output)
        except (LawAPIError, ValueError, OSError) as exc:
            parser.exit(1, f"조회 실패: {exc}\n")
        print(json.dumps(metadata, ensure_ascii=False, indent=2))
        print("원본 등록은 collect 명령으로 수행하세요.")
        return
    if args.store is None:
        parser.error("DB 관련 명령에는 --store가 필요합니다")
    store = PostgresStore(args.store)
    try:
        if args.command == "fee-normalize":
            result = normalize_fees(store.connection, args.artifact_sha256, args.mapping_version)
        elif args.command == "fee-search":
            result = search_fees(store.connection, code=args.code, name=args.name,
                                 artifact_sha256=args.artifact, limit=args.limit, offset=args.offset)
        elif args.command == "fee-xlsx-load":
            result = store.import_fees(args.rows, args.report)
        else:
            run_id = store.collect_files(args.source, args.files, args.scope) if args.command == "collect" else args.run_id
            result = store.report(run_id)
        print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
        if args.command == "collect" and result["failed"]:
            raise SystemExit(1)
    finally:
        store.close()


if __name__ == "__main__":
    main()
