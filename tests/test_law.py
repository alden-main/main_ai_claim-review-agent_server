"""행정규칙 응답 확인과 인증정보 보호를 검증한다."""

import os
import io
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from urllib.error import URLError

from main_ai_claim_review_agent_server.ingestion.law import LawAPIError, fetch_rule, inspect_rule_xml


class LawTest(unittest.TestCase):
    """고시 메타데이터와 실패 응답의 처리를 검증한다."""

    def test_rule_metadata_and_mismatch(self):
        data = "<행정규칙><행정규칙일련번호>123</행정규칙일련번호><행정규칙명>예시</행정규칙명><시행일자>20260101</시행일자></행정규칙>".encode()
        result = inspect_rule_xml(data, expected_id="123")
        self.assertEqual(result["effective_date_raw"], "20260101")
        self.assertEqual(result["scope_status"], "unverified")
        with self.assertRaises(LawAPIError):
            inspect_rule_xml(data, expected_id="456")
        with self.assertRaises(LawAPIError):
            inspect_rule_xml(b"<error>invalid key</error>")

    def test_fetch_preserves_bytes_and_refuses_overwrite(self):
        data = "<행정규칙><행정규칙일련번호>123</행정규칙일련번호><행정규칙명>예시</행정규칙명></행정규칙>".encode()
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "out.xml"
            with patch.dict(os.environ, {"LAW_API_OC": "secret"}), patch(
                "main_ai_claim_review_agent_server.ingestion.law.urllib.request.urlopen",
                side_effect=lambda *args, **kwargs: io.BytesIO(data),
            ):
                self.assertEqual(fetch_rule("123", output)["rule_id"], "123")
                with self.assertRaises(FileExistsError):
                    fetch_rule("123", output)
            self.assertEqual(output.read_bytes(), data)

    def test_error_does_not_expose_key_or_create_file(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "out.xml"
            with patch.dict(os.environ, {"LAW_API_OC": "secret"}), patch(
                "main_ai_claim_review_agent_server.ingestion.law.urllib.request.urlopen",
                side_effect=URLError("secret"),
            ):
                with self.assertRaises(LawAPIError) as error:
                    fetch_rule("123", output)
            self.assertNotIn("secret", str(error.exception))
            self.assertFalse(output.exists())
