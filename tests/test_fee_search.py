"""수가 검색어와 명시적인 결과 범위의 처리를 검증한다."""

import unittest
from unittest.mock import MagicMock

from main_ai_claim_review_agent_server.ingestion.fee_search import search_fees


class FeeSearchTest(unittest.TestCase):
    """수가코드와 명칭 검색 조건의 안전성을 검증한다."""

    def test_exact_code_and_pagination(self):
        connection = MagicMock()
        connection.execute.return_value.fetchall.return_value = [{'fee_code': '001'}, {'fee_code': '001'}]
        result = search_fees(connection, code='001', limit=1, artifact_sha256='hash')
        self.assertTrue(result['has_more'])
        self.assertEqual(result['results'][0]['fee_code'], '001')
        self.assertEqual(connection.execute.call_args.args[1], ('001', 'hash', 'hash', 2, 0))

    def test_literal_name_wildcards(self):
        connection = MagicMock()
        connection.execute.return_value.fetchall.return_value = []
        search_fees(connection, name=r'100%_\test')
        self.assertEqual(connection.execute.call_args.args[1][0], r'%100\%\_\\test%')

    def test_invalid_inputs(self):
        for args in ({}, {'code': 'a', 'name': 'b'}, {'code': ' '}, {'code': 'a', 'limit': 101}, {'code': 'a', 'offset': -1}):
            with self.assertRaises(ValueError):
                search_fees(MagicMock(), **args)
