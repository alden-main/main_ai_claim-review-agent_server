"""시범 수가 전체판의 시트별 원본 셀을 JSON Lines로 추출한다."""

import gzip
import hashlib
import json
import re
import xml.etree.ElementTree as ET
from pathlib import Path
from zipfile import ZipFile


MEDICAL = ['수가코드', '적용일자', '분류번호', '한글명', '영문명', '1-2 구분', '수술여부', '의원단가', '병원급이상단가', '치과병의원단가', '보건기관단가', '조산원단가', '한방병원단가', '요양·정신병원단가', '상대가치점수', '본인부담률50/100', '본인부담률80/100', '본인부담률90/100', '본인부담률95/100', '중복인정여부', '산정명칭', '장구분', '절구분', '세분류']
ORIENTAL = ['수가코드', '적용일자', '분류번호', '한글명', '산정명칭', '1_2구분', '시술구분', '한방병의원단가', '병원단가', '보건기관단가', '치과병원단가', '요양·정신병원', '상대가치점수', '장구분']
PHARMACY = ['수가코드', '한글명', '적용일자', '단가', '상대가치점수', '분류번호', '장구분']
SCHEMAS = {**{f'의치과_{kind}_전체': MEDICAL for kind in ('급여', '비급여', '100대100')},
           **{f'한방_{kind}_전체': ORIENTAL for kind in ('급여', '비급여', '100대100')},
           '약국_급여_전체': PHARMACY}
MAPPING_VERSION = 'sample-fee-20261101-v1'


def _shared_strings(archive: ZipFile) -> list[str]:
    """공유 문자열 표를 순서대로 읽어 셀의 문자열 참조를 해석한다.

    Args:
        archive: 열린 XLSX 압축 파일.
    Returns:
        인덱스로 조회할 수 있는 원본 문자열 목록.
    """
    if 'xl/sharedStrings.xml' not in archive.namelist():
        return []
    strings = []
    with archive.open('xl/sharedStrings.xml') as source:
        for _, node in ET.iterparse(source, events=('end',)):
            if node.tag.endswith('}si'):
                strings.append(''.join(x.text or '' for x in node.iter() if x.tag.endswith('}t')))
                node.clear()
    return strings


def _sheets(archive: ZipFile) -> dict[str, str]:
    """통합문서 관계를 따라 시트 이름과 XML 파일 경로를 연결한다.

    Args:
        archive: 열린 XLSX 압축 파일.
    Returns:
        시트 이름을 압축 파일 내부 경로에 대응한 사전.
    """
    workbook = ET.fromstring(archive.read('xl/workbook.xml'))
    relations = ET.fromstring(archive.read('xl/_rels/workbook.xml.rels'))
    paths = {item.get('Id'): item.get('Target') for item in relations}
    result = {}
    for sheet in workbook.findall('.//{*}sheet'):
        rid = sheet.get('{http://schemas.openxmlformats.org/officeDocument/2006/relationships}id')
        target = paths[rid]
        result[sheet.get('name')] = target.lstrip('/') if target.startswith('/') else 'xl/' + target
    return result


def _cell(cell: ET.Element, strings: list[str]) -> dict:
    """셀의 XML 값과 형식·수식을 원본 위치와 함께 읽는다.

    Args:
        cell: XLSX 워크시트의 셀 요소.
        strings: 공유 문자열 표.
    Returns:
        셀 주소, 유형, 스타일 ID, 원본 값과 수식 정보.
    """
    kind = cell.get('t', 'n')
    value = cell.find('{*}v')
    raw = value.text if value is not None else None
    if kind == 's' and raw is not None:
        raw = strings[int(raw)]
    elif kind == 'inlineStr':
        inline = cell.find('{*}is')
        raw = ''.join(x.text or '' for x in inline.iter() if x.tag.endswith('}t')) if inline is not None else None
    formula = cell.find('{*}f')
    return {'cell': cell.get('r'), 'type': kind, 'style': cell.get('s'), 'value': raw,
            **({'formula': formula.text or ''} if formula is not None else {})}


def _write_sheet(archive: ZipFile, path: str, name: str, strings: list[str], output, artifact_id: str) -> dict:
    """헤더를 대조하고 데이터 행과 검토 대상을 출력 스트림에 기록한다.

    Args:
        archive: 열린 원본 XLSX.
        path: 시트 XML의 압축 파일 내 경로.
        name: 원본 시트명.
        strings: 공유 문자열 표.
        output: JSON Lines 출력 스트림.
        artifact_id: 원본 파일의 SHA-256.
    Returns:
        해당 시트의 헤더와 데이터·빈 행·검토 대상 건수.
    """
    report = {'sheet': name, 'rows': 0, 'extracted': 0, 'review': 0, 'empty': 0}
    header = None
    with archive.open(path) as source:
        events = ET.iterparse(source, events=('start', 'end'))
        _, root = next(events)
        for event, row in events:
            if event != 'end' or not row.tag.endswith('}row'):
                continue
            number = int(row.get('r'))
            cells = [_cell(c, strings) for c in row.findall('{*}c')]
            if header is None:
                header = [c['value'] for c in cells]
                if number != 1 or header != SCHEMAS.get(name):
                    raise ValueError(f'시트 헤더가 예상과 다릅니다: {name}')
                report['headers'] = header
            else:
                report['rows'] += 1
                nonempty = [c for c in cells if c['value'] is not None or 'formula' in c]
                if not nonempty:
                    report['empty'] += 1
                else:
                    code = next((c for c in cells if c['cell'] == f'A{number}'), None)
                    invalid = any('formula' in c or c['type'] == 'e' or not re.fullmatch(r'[A-Z]+[0-9]+', c['cell'] or '') or (c['cell'] or '').rstrip('0123456789') not in {chr(65 + i) for i in range(len(header))} for c in cells)
                    review = invalid or not code or not code['value'] or code['type'] not in ('s', 'inlineStr', 'str') or len(cells) > len(header)
                    status = 'review' if review else 'extracted'
                    report[status] += 1
                    record = {'artifact_sha256': artifact_id, 'sheet': name, 'row': number,
                              'mapping_version': MAPPING_VERSION, 'status': status, 'cells': cells}
                    output.write(json.dumps(record, ensure_ascii=False, separators=(',', ':')) + '\n')
            row.clear()
            root.clear()
    if header is None:
        raise ValueError(f'빈 시트입니다: {name}')
    return report


def extract_workbook(source: Path, destination: Path) -> dict:
    """시범 수가 XLSX의 모든 시트를 압축 JSONL로 추출하고 집계한다.

    Args:
        source: 시범 수가 XLSX 파일 경로.
        destination: 새로 생성할 .jsonl.gz 출력 경로.
    Returns:
        원본 해시와 시트별 처리 건수·헤더를 담은 보고서.
    Raises:
        ValueError: 시트 또는 헤더가 예상과 다를 때.
        OSError: 입력 또는 출력 파일을 처리할 수 없을 때.
    """
    source, destination = Path(source), Path(destination)
    if source.resolve() == destination.resolve():
        raise ValueError('입력과 출력 경로가 같습니다')
    digest = hashlib.sha256()
    with source.open('rb') as data:
        for block in iter(lambda: data.read(1024 * 1024), b''):
            digest.update(block)
    report = {'artifact_sha256': digest.hexdigest(), 'mapping_version': MAPPING_VERSION,
              'quality_status': 'pending', 'completeness': 'unverified', 'sheets': []}
    with ZipFile(source) as archive:
        sheets = _sheets(archive)
        if set(sheets) != set(SCHEMAS):
            raise ValueError('시트 구성이 시범 수가 파일과 다릅니다')
        strings = _shared_strings(archive)
        with destination.open('xb') as target:
            try:
                with gzip.open(target, 'wt', encoding='utf-8') as output:
                    for name, path in sheets.items():
                        report['sheets'].append(_write_sheet(archive, path, name, strings, output, digest.hexdigest()))
            except BaseException:
                destination.unlink(missing_ok=True)
                raise
    return report
