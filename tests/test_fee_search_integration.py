"""격리된 PostgreSQL에서 수가 정규화와 검색을 검증한다."""

import gzip
import hashlib
import json
import os
import tempfile
import unittest
from unittest.mock import patch
from pathlib import Path

from main_ai_claim_review_agent_server.ingestion.fee_search import normalize_fees, search_fees
from main_ai_claim_review_agent_server.ingestion.fee_xlsx import MAPPING_VERSION, SCHEMAS
from main_ai_claim_review_agent_server.ingestion.postgres import PostgresStore


@unittest.skipUnless(os.environ.get('CLAIM_TEST_DATABASE_URL'), '격리된 PostgreSQL 테스트 주소가 필요합니다')
class FeeSearchIntegrationTest(unittest.TestCase):
    """원본 추적, 격리, 검색 및 재실행 결과를 확인한다."""

    def test_normalize_and_search(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / 'fixture.xlsx'
            source.write_bytes(b'fee search fixture')
            digest = hashlib.sha256(source.read_bytes()).hexdigest()
            sheets = [{'sheet': name, 'headers': columns, 'extracted': 0, 'review': 0} for name, columns in SCHEMAS.items()]
            sheets[0]['extracted'] = 2
            sheets[0]['review'] = 1
            sheets[-1]['extracted'] = 1
            report = {'artifact_sha256': digest, 'mapping_version': MAPPING_VERSION, 'sheets': sheets}
            def record(sheet, number, code, name, status='extracted', code_type='s'):
                name_column = 'B' if sheet == '약국_급여_전체' else 'D'
                return {'artifact_sha256': digest, 'mapping_version': MAPPING_VERSION, 'sheet': sheet,
                        'row': number, 'status': status, 'cells': [
                            {'cell': f'A{number}', 'type': code_type, 'value': code},
                            {'cell': f'{name_column}{number}', 'type': 's', 'value': name}]}
            records = [record(sheets[0]['sheet'], 2, '001', '진료 100% 검사'),
                       record(sheets[0]['sheet'], 3, '001', '진료 검사'),
                       record(sheets[0]['sheet'], 4, '42', '검토항목', 'review', 'n'),
                       record(sheets[-1]['sheet'], 2, 'P01', '약국 조제료')]
            rows = root / 'rows.gz'
            with gzip.open(rows, 'wt', encoding='utf8') as f:
                for row in records:
                    f.write(json.dumps(row, ensure_ascii=False) + '\n')
            report_path = root / 'report.json'
            report_path.write_text(json.dumps(report, ensure_ascii=False), encoding='utf8')
            with patch.dict(os.environ, {'DATABASE_URL': os.environ['CLAIM_TEST_DATABASE_URL']}):
                store = PostgresStore(root / 'raw')
                try:
                    store.collect_files('SRC-02', [source], 'test')
                    store.import_fees(rows, report_path)
                    first = normalize_fees(store.connection, digest)
                    self.assertEqual((first['total'], first['searchable'], first['review']), (4, 3, 1))
                    self.assertEqual(normalize_fees(store.connection, digest), first)
                    exact = search_fees(store.connection, code='001', artifact_sha256=digest, limit=1)
                    self.assertEqual(exact['results'][0]['fee_code'], '001')
                    self.assertTrue(exact['has_more'])
                    self.assertEqual(len(search_fees(store.connection, name='100%', artifact_sha256=digest)['results']), 1)
                    self.assertEqual(search_fees(store.connection, code='42', artifact_sha256=digest)['results'], [])
                    self.assertEqual(len(search_fees(store.connection, name='조제료', artifact_sha256=digest)['results']), 1)
                finally:
                    store.close()
