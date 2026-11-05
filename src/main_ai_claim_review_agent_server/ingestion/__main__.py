"""수동 확보 파일의 수집과 실행 보고 명령을 제공한다."""

import argparse
import json
from pathlib import Path

from .fee_api import FeeAPIError, fetch_pharmacy_page
from .law import LawAPIError, fetch_rule
from .store import RawStore


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
    args = parser.parse_args()
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
        parser.error("collect와 report에는 --store가 필요합니다")
    store = RawStore(args.store)
    try:
        run_id = store.collect_files(args.source, args.files, args.scope) if args.command == "collect" else args.run_id
        result = store.report(run_id)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        if args.command == "collect" and result["failed"]:
            raise SystemExit(1)
    finally:
        store.close()


if __name__ == "__main__":
    main()
