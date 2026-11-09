"""격리된 PostgreSQL에서 원본 수집 및 수가 적재를 검증한다."""

import gzip
import hashlib
import json
import os
import tempfile
import unittest
from pathlib import Path

from main_ai_claim_review_agent_server.ingestion.postgres import PostgresStore


@unittest.skipUnless(os.environ.get('DATABASE_URL'), 'PostgreSQL 테스트 주소가 필요합니다')
class PostgresTest(unittest.TestCase):
    """원본 등록과 동일 추출 결과 재적재의 정합성을 확인한다."""

    def test_collect_and_import_transaction(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / 'source.xlsx'
            source.write_bytes(b'postgres integration fixture')
            sha = hashlib.sha256(source.read_bytes()).hexdigest()
            row = {'artifact_sha256': sha, 'mapping_version': 'test-v1', 'sheet': 'test', 'row': 2,
                   'status': 'review', 'cells': [{'cell': 'A2', 'value': '001'}]}
            report = {'artifact_sha256': sha, 'mapping_version': 'test-v1',
                      'sheets': [{'extracted': 0, 'review': 1}]}
            rows = root / 'rows.gz'
            with gzip.open(rows, 'wt', encoding='utf8') as stream:
                stream.write(json.dumps(row) + '\n')
            report_path = root / 'report.json'
            report_path.write_text(json.dumps(report), encoding='utf8')
            store = PostgresStore(root / 'raw')
            try:
                run_id = store.collect_files('SRC-02', [source], 'fixture')
                self.assertEqual(store.report(run_id)['status'], 'COMPLETED')
                self.assertEqual(store.import_fees(rows, report_path)['processed'], 1)
                self.assertEqual(store.import_fees(rows, report_path)['processed'], 1)
                count = store.connection.execute('SELECT count(*) AS n FROM fee_rows WHERE artifact_sha256=%s', (sha,)).fetchone()['n']
                self.assertEqual(count, 1)
                row['cells'][0]['value'] = '002'
                with gzip.open(rows, 'wt', encoding='utf8') as stream:
                    stream.write(json.dumps(row) + '\n')
                with self.assertRaises(ValueError):
                    store.import_fees(rows, report_path)
                self.assertEqual(store.connection.execute('SELECT count(*) AS n FROM fee_rows WHERE artifact_sha256=%s', (sha,)).fetchone()['n'], 1)
            finally:
                store.close()
