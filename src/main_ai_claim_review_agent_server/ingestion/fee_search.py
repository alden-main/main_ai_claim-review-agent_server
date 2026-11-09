"""시범 수가의 검색용 필드를 정규화하고 코드·명칭으로 조회한다."""

from .fee_xlsx import MAPPING_VERSION, SCHEMAS

NORMALIZATION_VERSION = 'fee-search-v1'

DDL = """
CREATE EXTENSION IF NOT EXISTS pg_trgm;
CREATE TABLE IF NOT EXISTS fee_search_items (
    artifact_sha256 text NOT NULL, sheet text NOT NULL, row_number integer NOT NULL,
    mapping_version text NOT NULL, normalization_version text NOT NULL,
    fee_code text, fee_name text, source_status text NOT NULL,
    search_status text NOT NULL, quality_status text NOT NULL DEFAULT 'pending',
    period_status text NOT NULL DEFAULT 'unverified',
    PRIMARY KEY (artifact_sha256, sheet, row_number, mapping_version, normalization_version),
    FOREIGN KEY (artifact_sha256, sheet, row_number, mapping_version)
      REFERENCES fee_rows (artifact_sha256, sheet, row_number, mapping_version)
);
CREATE INDEX IF NOT EXISTS fee_search_items_code_idx ON fee_search_items (fee_code);
CREATE INDEX IF NOT EXISTS fee_search_items_name_trgm_idx ON fee_search_items USING gin (fee_name gin_trgm_ops);
"""


def normalize_fees(connection, artifact_sha256: str, mapping_version: str = MAPPING_VERSION) -> dict:
    """확인된 시트 매핑으로 원본 행의 코드·명칭을 검색용 테이블에 기록한다.

    Args:
        connection: psycopg PostgreSQL 연결.
        artifact_sha256: 처리할 원본 파일의 SHA-256.
        mapping_version: 원본 추출에 사용한 매핑 버전.
    Returns:
        원본 행과 검색 가능·검토 필요 행의 건수 및 변환 버전.
    Raises:
        ValueError: 매핑, 헤더, 원본 건수 또는 기존 변환 결과가 일치하지 않을 때.
    """
    if mapping_version != MAPPING_VERSION:
        raise ValueError('지원하지 않는 원본 매핑 버전입니다')
    with connection.transaction():
        report_row = connection.execute('SELECT report FROM fee_imports WHERE artifact_sha256=%s AND mapping_version=%s', (artifact_sha256, mapping_version)).fetchone()
        if not report_row:
            raise ValueError('원본 추출 보고서를 찾을 수 없습니다')
        report = report_row['report']
        sheets = {entry['sheet']: entry for entry in report['sheets']}
        if len(sheets) != len(report['sheets']) or set(sheets) != set(SCHEMAS) or any(sheets[name].get('headers') != header for name, header in SCHEMAS.items()):
            raise ValueError('원본 보고서의 시트 또는 헤더가 확인된 매핑과 다릅니다')
        expected = sum(s['extracted'] + s['review'] for s in sheets.values())
        count = connection.execute('SELECT count(*) AS n FROM fee_rows WHERE artifact_sha256=%s AND mapping_version=%s', (artifact_sha256, mapping_version)).fetchone()['n']
        if count != expected:
            raise ValueError('원본 행 수가 추출 보고서와 다릅니다')
        connection.execute(DDL)
        connection.execute('CREATE TEMP TABLE normalized_fee_staging (LIKE fee_search_items INCLUDING DEFAULTS INCLUDING CONSTRAINTS INCLUDING INDEXES) ON COMMIT DROP')
        connection.execute("""
            INSERT INTO normalized_fee_staging
              (artifact_sha256, sheet, row_number, mapping_version, normalization_version,
               fee_code, fee_name, source_status, search_status)
            SELECT f.artifact_sha256, f.sheet, f.row_number, f.mapping_version, %s,
                   x.code, x.name, f.status,
                   CASE WHEN f.status = 'extracted' AND x.code_count = 1 AND x.name_count = 1
                             AND x.code_valid = 1 AND x.name_valid = 1
                             AND x.code IS NOT NULL AND x.name IS NOT NULL
                        THEN 'searchable' ELSE 'review' END
            FROM fee_rows f
            CROSS JOIN LATERAL (
                SELECT
                  count(*) FILTER (WHERE c->>'cell' = 'A' || f.row_number) AS code_count,
                  count(*) FILTER (WHERE c->>'cell' = (CASE WHEN f.sheet = '약국_급여_전체' THEN 'B' ELSE 'D' END) || f.row_number) AS name_count,
                  count(*) FILTER (WHERE c->>'cell' = 'A' || f.row_number AND c->>'type' IN ('s','inlineStr','str') AND NOT c ? 'formula') AS code_valid,
                  count(*) FILTER (WHERE c->>'cell' = (CASE WHEN f.sheet = '약국_급여_전체' THEN 'B' ELSE 'D' END) || f.row_number AND c->>'type' IN ('s','inlineStr','str') AND NOT c ? 'formula') AS name_valid,
                  max(NULLIF(btrim(c->>'value'), '')) FILTER (WHERE c->>'cell' = 'A' || f.row_number) AS code,
                  max(NULLIF(btrim(c->>'value'), '')) FILTER (WHERE c->>'cell' = (CASE WHEN f.sheet = '약국_급여_전체' THEN 'B' ELSE 'D' END) || f.row_number) AS name
                FROM jsonb_array_elements(f.cells) AS c
            ) x
            WHERE f.artifact_sha256=%s AND f.mapping_version=%s
        """, (NORMALIZATION_VERSION, artifact_sha256, mapping_version))
        conflict = connection.execute("""SELECT count(*) AS n FROM normalized_fee_staging s
            JOIN fee_search_items d USING (artifact_sha256,sheet,row_number,mapping_version,normalization_version)
            WHERE (s.fee_code,s.fee_name,s.source_status,s.search_status) IS DISTINCT FROM
                  (d.fee_code,d.fee_name,d.source_status,d.search_status)""").fetchone()['n']
        if conflict:
            raise ValueError('같은 변환 버전의 기존 결과와 값이 다릅니다')
        connection.execute('INSERT INTO fee_search_items SELECT * FROM normalized_fee_staging ON CONFLICT DO NOTHING')
        stats = connection.execute("""SELECT count(*) AS total,
            count(*) FILTER (WHERE search_status='searchable') AS searchable,
            count(*) FILTER (WHERE search_status='review') AS review
            FROM normalized_fee_staging""").fetchone()
    return {'artifact_sha256': artifact_sha256, 'mapping_version': mapping_version,
            'normalization_version': NORMALIZATION_VERSION, **dict(stats), 'quality_status': 'pending'}


def search_fees(connection, *, code: str | None = None, name: str | None = None,
                artifact_sha256: str | None = None, limit: int = 20, offset: int = 0) -> dict:
    """수가코드 정확 일치 또는 명칭 부분 일치로 검색한다.

    Args:
        connection: psycopg PostgreSQL 연결.
        code: 정확히 일치할 수가코드. 명칭과 함께 지정할 수 없다.
        name: 포함 여부를 찾을 수가 명칭. SQL 와일드카드는 문자 그대로 취급한다.
        artifact_sha256: 결과를 제한할 원본 해시. 생략 시 모든 원본 버전을 검색한다.
        limit: 반환할 최대 건수(1~100).
        offset: 건너뛸 결과 수(0 이상).
    Returns:
        원본 위치와 미확인 상태를 포함한 검색 결과 및 추가 결과 여부.
    """
    if (code is None) == (name is None) or not 1 <= limit <= 100 or offset < 0:
        raise ValueError('코드와 명칭 중 하나, 1~100의 결과 수와 0 이상의 시작 위치를 지정하세요')
    term = (code if code is not None else name).strip()
    if not term:
        raise ValueError('검색어는 비어 있을 수 없습니다')
    if name is not None:
        term = '%' + term.replace('\\', '\\\\').replace('%', '\\%').replace('_', '\\_') + '%'
    condition = "fee_code = %s" if code is not None else "fee_name ILIKE %s ESCAPE E'\\\\'"
    rows = connection.execute(f"""SELECT artifact_sha256,sheet,row_number,mapping_version,
        normalization_version,fee_code,fee_name,search_status,quality_status,period_status
        FROM fee_search_items WHERE search_status='searchable' AND {condition}
        AND (%s::text IS NULL OR artifact_sha256=%s)
        ORDER BY fee_code, artifact_sha256, sheet, row_number, mapping_version, normalization_version LIMIT %s OFFSET %s""",
        (term, artifact_sha256, artifact_sha256, limit + 1, offset)).fetchall()
    return {'results': rows[:limit], 'has_more': len(rows) > limit,
            'scope': 'all_artifacts' if artifact_sha256 is None else artifact_sha256,
            'offset': offset, 'next_offset': offset + limit if len(rows) > limit else None,
            'note': '검색 결과는 품질 검토 대기이며 적용기간과 사용 승인은 확인되지 않았습니다.'}
