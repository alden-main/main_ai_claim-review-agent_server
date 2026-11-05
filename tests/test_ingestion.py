"""원본 수집 이력과 보수적인 필드 변환을 검증한다."""

import tempfile
import unittest
from pathlib import Path

from main_ai_claim_review_agent_server.ingestion.normalize import FieldMapping, normalize_record
from main_ai_claim_review_agent_server.ingestion.store import RawStore


class RawStoreTest(unittest.TestCase):
    """원본 중복, 변경 및 부분 실패 이력을 검증한다."""

    def test_observations_failures_and_retry(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            first = root / "first.txt"
            second = root / "second.txt"
            first.write_bytes(b"same")
            second.write_bytes(b"same")
            store = RawStore(root / "store")
            try:
                run = store.collect_files("sample", [first, second, root / "missing"], "sample only")
                report = store.report(run)
                self.assertEqual((report["status"], report["succeeded"], report["failed"]), ("PARTIAL_SUCCESS", 2, 1))
                self.assertEqual(report["completeness"], "unverified")
                self.assertEqual(store.connection.execute("SELECT count(*) FROM artifacts").fetchone()[0], 1)
                self.assertEqual(store.connection.execute("SELECT count(*) FROM observations").fetchone()[0], 2)
                second.write_bytes(b"changed")
                retry = store.collect_files("sample", [second], "retry")
                self.assertEqual(store.report(retry)["status"], "COMPLETED")
                self.assertEqual(store.connection.execute("SELECT count(*) FROM artifacts").fetchone()[0], 2)
            finally:
                store.close()


class NormalizeTest(unittest.TestCase):
    """원본 값과 변환 오류의 보존 여부를 검증한다."""

    def test_values_and_missing_states(self):
        mappings = {"code": FieldMapping("code", required=True), "score": FieldMapping("score", "decimal"),
                    "amount": FieldMapping("amount", "decimal"), "date": FieldMapping("date")}
        result = normalize_record({"code": "001", "score": "16.260000000000002", "amount": None}, mappings,
                                  artifact_id="hash", location="sheet:2", mapping_version="v1")
        self.assertEqual(result["fields"]["code"]["value"], "001")
        self.assertEqual(result["fields"]["score"]["value"], "16.260000000000002")
        self.assertEqual(result["fields"]["amount"]["status"], "not_provided")
        self.assertEqual(result["fields"]["date"]["status"], "unverified")
        self.assertEqual(result["quality_status"], "pending")

    def test_invalid_values_are_quarantined(self):
        result = normalize_record({"code": 12, "score": 1.2},
                                  {"code": FieldMapping("code", required=True), "score": FieldMapping("score", "decimal")},
                                  artifact_id="hash", location="row:1", mapping_version="v1")
        self.assertEqual(result["quality_status"], "failed")
        self.assertEqual(len(result["issues"]), 2)
        self.assertEqual(result["fields"]["code"]["raw"], 12)


if __name__ == "__main__":
    unittest.main()
