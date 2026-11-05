"""약국수가 API의 요청 구성과 응답 검증을 확인한다."""

import io
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from urllib.error import URLError
from urllib.parse import parse_qs, urlsplit

from main_ai_claim_review_agent_server.ingestion.fee_api import FeeAPIError, fetch_pharmacy_page, inspect_fee_response


class FeeAPITest(unittest.TestCase):
    """합성 응답으로 원본 보존과 오류 처리를 확인한다."""

    def test_fetch_request_and_raw_response(self):
        data = b"<response><header><resultCode>00</resultCode></header><body><items><item><mdfeeCd>001</mdfeeCd></item></items><totalCount>1</totalCount></body></response>"
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, {"HIRA_SERVICE_KEY": "a+b/="}):
            path = Path(directory) / "page.xml"
            with patch("main_ai_claim_review_agent_server.ingestion.fee_api.urllib.request.build_opener") as opener:
                opener.return_value.open.side_effect = lambda *args, **kwargs: io.BytesIO(data)
                result = fetch_pharmacy_page(path, code="001", page=2)
                request = opener.return_value.open.call_args.args[0]
                query = parse_qs(urlsplit(request.full_url).query)
                self.assertEqual(query["ServiceKey"], ["a+b/="])
                self.assertEqual(query["mdfeeCd"], ["001"])
                self.assertNotIn("ServiceKey", result["request"])
                self.assertEqual(result["item_count"], 1)
                self.assertEqual(result["completeness"], "unverified")
                self.assertEqual(path.read_bytes(), data)
                with self.assertRaises(FileExistsError):
                    fetch_pharmacy_page(path)

    def test_bad_responses_and_secret_redaction(self):
        for data in (b"not xml", b"<response><header><resultCode>30</resultCode></header></response>",
                     b"<response><header><resultCode>00</resultCode></header></response>"):
            with self.assertRaises(FeeAPIError):
                inspect_fee_response(data)
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, {"HIRA_SERVICE_KEY": "secret"}):
            with patch("main_ai_claim_review_agent_server.ingestion.fee_api.urllib.request.build_opener") as opener:
                opener.return_value.open.side_effect = URLError("secret")
                with self.assertRaises(FeeAPIError) as error:
                    fetch_pharmacy_page(Path(directory) / "page.xml")
                self.assertNotIn("secret", str(error.exception))
                self.assertFalse((Path(directory) / "page.xml").exists())
