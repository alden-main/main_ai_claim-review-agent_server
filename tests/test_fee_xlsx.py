"""시범 수가 워크시트의 원본 추출과 오류 격리를 검증한다."""

import io
import json
import unittest
from zipfile import ZipFile

from main_ai_claim_review_agent_server.ingestion.fee_xlsx import PHARMACY, _write_sheet


class FeeXlsxTest(unittest.TestCase):
    """헤더와 원본 셀의 추적성 및 미확인 값을 검증한다."""

    def test_rows_and_quarantine(self):
        header = ''.join(f'<c r="{chr(65+i)}1" t="inlineStr"><is><t>{v}</t></is></c>' for i,v in enumerate(PHARMACY))
        xml = f'''<worksheet xmlns="urn:x"><sheetData><row r="1">{header}</row>
        <row r="2"><c r="A2" t="inlineStr"><is><t>001</t></is></c><c r="E2"><v>16.260000000000002</v></c></row>
        <row r="3"><c r="A3"><v>12</v></c></row><row r="4"/></sheetData></worksheet>'''
        buffer = io.BytesIO()
        with ZipFile(buffer, 'w') as archive:
            archive.writestr('sheet.xml', xml)
        output = io.StringIO()
        with ZipFile(buffer) as archive:
            report = _write_sheet(archive, 'sheet.xml', '약국_급여_전체', [], output, 'hash')
        records = [json.loads(line) for line in output.getvalue().splitlines()]
        self.assertEqual((report['rows'], report['extracted'], report['review'], report['empty']), (3, 1, 1, 1))
        self.assertEqual(records[0]['cells'][0]['value'], '001')
        self.assertEqual(records[0]['cells'][1]['value'], '16.260000000000002')
        self.assertEqual(records[1]['status'], 'review')

    def test_unknown_header_fails(self):
        buffer = io.BytesIO()
        with ZipFile(buffer, 'w') as archive:
            archive.writestr('s.xml', '<worksheet xmlns="urn:x"><sheetData><row r="1"><c r="A1" t="inlineStr"><is><t>다른 헤더</t></is></c></row></sheetData></worksheet>')
        with ZipFile(buffer) as archive, self.assertRaises(ValueError):
            _write_sheet(archive, 's.xml', '약국_급여_전체', [], io.StringIO(), 'hash')
