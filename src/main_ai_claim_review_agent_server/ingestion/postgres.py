"""PostgreSQL에 수집 이력과 수가 추출 행을 보존한다."""

import gzip
import hashlib
import json
import os
from pathlib import Path
from uuid import uuid4

from .store import RawStore, _now

SCHEMA = """
CREATE TABLE IF NOT EXISTS sources (
    id text PRIMARY KEY, organization text NOT NULL, channel text NOT NULL,
    url text, provenance_status text NOT NULL
);
CREATE TABLE IF NOT EXISTS runs (
    id text PRIMARY KEY, source_id text NOT NULL, started_at timestamptz NOT NULL,
    finished_at timestamptz, status text NOT NULL, scope text NOT NULL,
    requested integer NOT NULL DEFAULT 0, succeeded integer NOT NULL DEFAULT 0,
    failed integer NOT NULL DEFAULT 0, completeness text NOT NULL DEFAULT 'unverified'
);
CREATE TABLE IF NOT EXISTS artifacts (sha256 text PRIMARY KEY, size bigint NOT NULL, path text NOT NULL);
CREATE TABLE IF NOT EXISTS observations (
    id text PRIMARY KEY, run_id text NOT NULL REFERENCES runs(id),
    sha256 text NOT NULL REFERENCES artifacts(sha256), original_path text NOT NULL, observed_at timestamptz NOT NULL
);
CREATE TABLE IF NOT EXISTS failures (
    id text PRIMARY KEY, run_id text NOT NULL REFERENCES runs(id),
    original_path text NOT NULL, reason text NOT NULL, occurred_at timestamptz NOT NULL
);
CREATE TABLE IF NOT EXISTS artifact_source_evidence (
    artifact_sha256 text NOT NULL REFERENCES artifacts(sha256),
    source_id text NOT NULL REFERENCES sources(id),
    reference_url text NOT NULL, comparison_path text NOT NULL,
    comparison_sha256 text NOT NULL, method text NOT NULL,
    provenance_basis text NOT NULL, license_status text NOT NULL DEFAULT 'unverified',
    verified_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (artifact_sha256, source_id, reference_url, comparison_sha256)
);
CREATE TABLE IF NOT EXISTS fee_rows (
    artifact_sha256 text NOT NULL REFERENCES artifacts(sha256), sheet text NOT NULL,
    row_number integer NOT NULL, mapping_version text NOT NULL, status text NOT NULL,
    cells jsonb NOT NULL, PRIMARY KEY (artifact_sha256, sheet, row_number, mapping_version)
);
CREATE TABLE IF NOT EXISTS fee_imports (
    artifact_sha256 text NOT NULL REFERENCES artifacts(sha256), mapping_version text NOT NULL,
    report jsonb NOT NULL, imported_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (artifact_sha256, mapping_version)
);
"""


class PostgresStore(RawStore):
    """원본 파일은 보존 경로에 두고 이력과 추출 행을 PostgreSQL에 저장한다."""

    def __init__(self, root: Path):
        """환경변수로 DB에 연결하고 필요한 테이블과 출처 후보를 준비한다.

        Args:
            root: 해시별 원본 파일을 보관할 디렉터리.
        Returns:
            없음. 연결 또는 스키마 오류는 호출자에게 전달한다.
        """
        import psycopg
        from psycopg.rows import dict_row
        dsn = os.environ.get('DATABASE_URL')
        if not dsn:
            raise ValueError('DATABASE_URL 환경변수가 필요합니다')
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.connection = psycopg.connect(dsn, row_factory=dict_row, autocommit=True)
        with self.connection.transaction():
            self.connection.execute(SCHEMA)
            self.connection.execute("""INSERT INTO sources VALUES
                ('SRC-01', '건강보험심사평가원', '공공데이터포털 API', 'https://www.data.go.kr/data/15021028/openapi.do', 'documented'),
                ('SRC-02', '건강보험심사평가원', '심평원 자료실', 'https://www.hira.or.kr/rc/drug/insuadtcrtr/bbsView.do?brdBltNo=53217&brdScnBltNo=4&pgmid=HIRAA030069000400', 'candidate'),
                ('SRC-03', '법제처', '국가법령정보센터', 'https://www.law.go.kr/', 'documented')
                ON CONFLICT (id) DO NOTHING""")

    def collect_files(self, source_id: str, paths: list[Path], scope: str) -> str:
        """수동 파일을 수집하고 PostgreSQL에 실행별 성공과 실패를 기록한다.

        Args:
            source_id: 출처 식별자. 샘플의 입수 경로는 범위에 명시한다.
            paths: 원본 파일 경로 목록.
            scope: 수집 대상 및 입수 경로 설명.
        Returns:
            생성된 실행 ID.
        """
        if not source_id.strip() or not scope.strip() or not paths:
            raise ValueError('출처, 수집 범위와 하나 이상의 파일이 필요합니다')
        run_id = uuid4().hex
        with self.connection.transaction():
            self.connection.execute('INSERT INTO runs (id,source_id,started_at,status,scope,requested) VALUES (%s,%s,%s,%s,%s,%s)',
                                    (run_id, source_id, _now(), 'INGESTING', scope, len(paths)))
        succeeded = 0
        for path in paths:
            try:
                self._collect_file(run_id, Path(path))
                succeeded += 1
            except OSError as exc:
                with self.connection.transaction():
                    self.connection.execute('INSERT INTO failures VALUES (%s,%s,%s,%s,%s)',
                                            (uuid4().hex, run_id, str(path), str(exc), _now()))
        failed = len(paths) - succeeded
        status = 'COMPLETED' if not failed else 'PARTIAL_SUCCESS' if succeeded else 'FAILED'
        with self.connection.transaction():
            self.connection.execute('UPDATE runs SET finished_at=%s,status=%s,succeeded=%s,failed=%s WHERE id=%s',
                                    (_now(), status, succeeded, failed, run_id))
        return run_id

    def _collect_file(self, run_id: str, path: Path) -> None:
        """원본 해시를 확인해 파일을 보관하고 관측을 DB에 추가한다.

        Args:
            run_id: 수집 실행 ID.
            path: 수집할 원본 파일.
        Returns:
            없음. 파일 오류나 DB 오류는 호출자에게 전달한다.
        """
        import tempfile
        data = path.read_bytes()
        digest = hashlib.sha256(data).hexdigest()
        relative = Path('raw') / digest[:2] / digest
        target = self.root / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        if not target.exists():
            temporary = None
            try:
                with tempfile.NamedTemporaryFile(dir=target.parent, delete=False) as output:
                    temporary = Path(output.name)
                    output.write(data)
                try:
                    os.link(temporary, target)
                except FileExistsError:
                    pass
            finally:
                if temporary:
                    temporary.unlink(missing_ok=True)
        if hashlib.sha256(target.read_bytes()).hexdigest() != digest:
            raise OSError('기존 원본의 해시가 일치하지 않습니다')
        with self.connection.transaction():
            self.connection.execute('INSERT INTO artifacts VALUES (%s,%s,%s) ON CONFLICT (sha256) DO NOTHING', (digest, len(data), str(relative)))
            self.connection.execute('INSERT INTO observations VALUES (%s,%s,%s,%s,%s)', (uuid4().hex, run_id, digest, str(path), _now()))

    def report(self, run_id: str) -> dict:
        """수집 실행 결과와 실패 경로를 조회한다.

        Args:
            run_id: 조회할 실행 ID.
        Returns:
            실행 정보와 실패 목록. 실행이 없으면 KeyError가 발생한다.
        """
        row = self.connection.execute('SELECT * FROM runs WHERE id=%s', (run_id,)).fetchone()
        if row is None:
            raise KeyError(run_id)
        result = dict(row)
        result['failures'] = list(self.connection.execute('SELECT original_path,reason,occurred_at FROM failures WHERE run_id=%s', (run_id,)))
        return result

    def record_source_comparison(self, artifact_sha256: str, comparison_path: Path, *,
                                 source_id: str, reference_url: str) -> dict:
        """보관 원본과 사용자가 제공한 비교 파일을 해시 대조해 출처 근거를 기록한다.

        Args:
            artifact_sha256: DB에 등록된 원본의 SHA-256.
            comparison_path: 사용자가 해당 게시물에서 내려받았다고 제공한 파일.
            source_id: 등록된 출처 식별자.
            reference_url: 사용자가 밝힌 다운로드 게시물 주소.
        Returns:
            두 해시가 일치한 원본 식별자와 검증 방식.
        Raises:
            ValueError: 원본이 없거나 보관 파일 또는 비교 파일의 해시가 다를 때.
        """
        if not reference_url.startswith('https://www.hira.or.kr/') or source_id != 'SRC-02':
            raise ValueError('심평원 자료실 출처와 HTTPS 게시물 주소가 필요합니다')
        row = self.connection.execute('SELECT path FROM artifacts WHERE sha256=%s', (artifact_sha256,)).fetchone()
        if row is None:
            raise ValueError('등록된 원본을 찾을 수 없습니다')
        def digest(path: Path) -> str:
            with path.open('rb') as stream:
                return hashlib.file_digest(stream, 'sha256').hexdigest()
        if digest(self.root / row['path']) != artifact_sha256 or digest(Path(comparison_path)) != artifact_sha256:
            raise ValueError('보관 원본 또는 비교 파일의 해시가 일치하지 않습니다')
        with self.connection.transaction():
            self.connection.execute("""INSERT INTO artifact_source_evidence
                (artifact_sha256, source_id, reference_url, comparison_path, comparison_sha256, method, provenance_basis)
                VALUES (%s,%s,%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING""",
                (artifact_sha256, source_id, reference_url, str(comparison_path), artifact_sha256,
                 'sha256_match', 'user_supplied_download'))
        return {'artifact_sha256': artifact_sha256, 'method': 'sha256_match',
                'provenance_basis': 'user_supplied_download', 'license_status': 'unverified'}

    def import_fees(self, rows_path: Path, report_path: Path) -> dict:
        """등록된 원본의 추출 행을 단일 트랜잭션으로 중복 없이 적재한다.

        Args:
            rows_path: 추출된 gzip JSON Lines 경로.
            report_path: 같은 추출 실행의 집계 보고서 경로.
        Returns:
            처리 행 수와 신규 저장 행 수.
        Raises:
            ValueError: 원본, 매핑 버전 또는 보고서 건수가 일치하지 않는 경우.
        """
        from psycopg.types.json import Jsonb
        report = json.loads(Path(report_path).read_text(encoding='utf-8'))
        artifact = report['artifact_sha256']
        version = report['mapping_version']
        expected = sum(s['extracted'] + s['review'] for s in report['sheets'])
        count = 0
        with self.connection.transaction():
            if not self.connection.execute('SELECT 1 FROM artifacts WHERE sha256=%s', (artifact,)).fetchone():
                raise ValueError('먼저 추출 원본 XLSX를 collect로 등록해야 합니다')
            self.connection.execute('CREATE TEMP TABLE fee_staging (LIKE fee_rows INCLUDING DEFAULTS INCLUDING CONSTRAINTS INCLUDING INDEXES) ON COMMIT DROP')
            with self.connection.cursor().copy('COPY fee_staging (artifact_sha256,sheet,row_number,mapping_version,status,cells) FROM STDIN') as copy:
                with gzip.open(rows_path, 'rt', encoding='utf-8') as source:
                    for line in source:
                        row = json.loads(line)
                        if row['artifact_sha256'] != artifact or row['mapping_version'] != version:
                            raise ValueError('원본 해시 또는 매핑 버전이 보고서와 다릅니다')
                        copy.write_row((artifact, row['sheet'], row['row'], version, row['status'], Jsonb(row['cells'])))
                        count += 1
            if count != expected:
                raise ValueError('추출 행 수가 보고서와 다릅니다')
            self.connection.execute('INSERT INTO fee_rows SELECT * FROM fee_staging ON CONFLICT DO NOTHING')
            conflicts = self.connection.execute('SELECT count(*) AS n FROM fee_staging s WHERE EXISTS (SELECT 1 FROM fee_rows f WHERE f.artifact_sha256=s.artifact_sha256 AND f.sheet=s.sheet AND f.row_number=s.row_number AND f.mapping_version=s.mapping_version AND (f.status<>s.status OR f.cells<>s.cells))').fetchone()['n']
            if conflicts:
                raise ValueError('동일 원본·버전에 다른 추출 결과가 존재합니다')
            self.connection.execute('INSERT INTO fee_imports (artifact_sha256,mapping_version,report) VALUES (%s,%s,%s) ON CONFLICT DO NOTHING', (artifact, version, Jsonb(report)))
        return {'artifact_sha256': artifact, 'mapping_version': version, 'processed': count, 'quality_status': 'pending'}
