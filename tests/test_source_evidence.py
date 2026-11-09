"""수동 다운로드 파일과 보관 원본의 출처 대조를 검증한다."""

import hashlib
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock

from main_ai_claim_review_agent_server.ingestion.postgres import PostgresStore


class SourceEvidenceTest(unittest.TestCase):
    """양쪽 해시가 모두 일치할 때만 출처 근거를 기록한다."""

    def test_record_and_reject_mismatch(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            original, downloaded = root / 'raw', root / 'downloaded'
            original.write_bytes(b'original')
            downloaded.write_bytes(b'original')
            digest = hashlib.sha256(b'original').hexdigest()
            store = PostgresStore.__new__(PostgresStore)
            store.root = root
            store.connection = MagicMock()
            store.connection.execute.return_value.fetchone.return_value = {'path': 'raw'}
            result = store.record_source_comparison(digest, downloaded, source_id='SRC-02', reference_url='https://www.hira.or.kr/board')
            self.assertEqual(result['license_status'], 'unverified')
            self.assertEqual(result['provenance_basis'], 'user_supplied_download')
            self.assertEqual(store.connection.execute.call_count, 2)
            downloaded.write_bytes(b'changed')
            with self.assertRaises(ValueError):
                store.record_source_comparison(digest, downloaded, source_id='SRC-02', reference_url='https://www.hira.or.kr/board')
            self.assertEqual(store.connection.execute.call_count, 3)
