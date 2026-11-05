"""수동 확보 원본과 실행·관측 이력을 로컬 저장소에 보관한다."""

import hashlib
import sqlite3
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class RawStore:
    """내용 해시로 원본을 보관하고 수집 관측과 실패 이력을 기록한다."""

    def __init__(self, root: Path):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.connection = sqlite3.connect(self.root / "catalog.sqlite3")
        self.connection.execute("PRAGMA foreign_keys = ON")
        self.connection.executescript("""
            CREATE TABLE IF NOT EXISTS runs (
                id TEXT PRIMARY KEY, source_id TEXT NOT NULL, started_at TEXT NOT NULL,
                finished_at TEXT, status TEXT NOT NULL, scope TEXT NOT NULL,
                requested INTEGER NOT NULL DEFAULT 0, succeeded INTEGER NOT NULL DEFAULT 0,
                failed INTEGER NOT NULL DEFAULT 0, completeness TEXT NOT NULL DEFAULT 'unverified'
            );
            CREATE TABLE IF NOT EXISTS artifacts (
                sha256 TEXT PRIMARY KEY, size INTEGER NOT NULL, path TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS observations (
                id TEXT PRIMARY KEY, run_id TEXT NOT NULL REFERENCES runs(id),
                sha256 TEXT NOT NULL REFERENCES artifacts(sha256), original_path TEXT NOT NULL,
                observed_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS failures (
                id TEXT PRIMARY KEY, run_id TEXT NOT NULL REFERENCES runs(id),
                original_path TEXT NOT NULL, reason TEXT NOT NULL, occurred_at TEXT NOT NULL
            );
        """)

    def close(self) -> None:
        self.connection.close()

    def collect_files(self, source_id: str, paths: list[Path], scope: str) -> str:
        """파일별 수집 결과를 기록하고 해당 실행 범위의 처리 상태를 확정한다.

        Args:
            source_id: 수동 확보 자료의 출처 식별자.
            paths: 수집할 원본 파일 경로 목록.
            scope: 확인 가능한 수집 범위를 설명하는 문자열.
        Returns:
            생성된 수집 실행 ID. 원천 전체성은 항상 미확인으로 남는다.
        """
        if not source_id.strip() or not scope.strip() or not paths:
            raise ValueError("출처, 수집 범위와 하나 이상의 파일이 필요합니다")
        run_id = uuid4().hex
        with self.connection:
            self.connection.execute(
                "INSERT INTO runs (id, source_id, started_at, status, scope, requested) VALUES (?, ?, ?, ?, ?, ?)",
                (run_id, source_id, _now(), "INGESTING", scope, len(paths)),
            )
        succeeded = 0
        for path in paths:
            try:
                self._collect_file(run_id, Path(path))
                succeeded += 1
            except OSError as exc:
                with self.connection:
                    self.connection.execute(
                        "INSERT INTO failures VALUES (?, ?, ?, ?, ?)",
                        (uuid4().hex, run_id, str(path), str(exc), _now()),
                    )
        failed = len(paths) - succeeded
        status = "COMPLETED" if not failed else "PARTIAL_SUCCESS" if succeeded else "FAILED"
        with self.connection:
            self.connection.execute(
                "UPDATE runs SET finished_at=?, status=?, succeeded=?, failed=? WHERE id=?",
                (_now(), status, succeeded, failed, run_id),
            )
        return run_id

    def _collect_file(self, run_id: str, path: Path) -> None:
        """파일 바이트의 해시를 검증해 저장하고 별도의 관측을 추가한다.

        Args:
            run_id: 현재 수집 실행 ID.
            path: 수동으로 확보한 원본 파일 경로.
        Returns:
            없음. 저장 오류는 호출자에게 전달한다.
        """
        data = path.read_bytes()
        digest = hashlib.sha256(data).hexdigest()
        relative = Path("raw") / digest[:2] / digest
        target = self.root / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.exists():
            if hashlib.sha256(target.read_bytes()).hexdigest() != digest:
                raise OSError("기존 원본의 해시가 일치하지 않습니다")
        else:
            # 임시 파일을 완전히 쓴 후 연결해 부분 원본이 남지 않게 한다.
            temporary = None
            try:
                with tempfile.NamedTemporaryFile(dir=target.parent, delete=False) as output:
                    temporary = Path(output.name)
                    output.write(data)
                try:
                    os.link(temporary, target)
                except FileExistsError:
                    if hashlib.sha256(target.read_bytes()).hexdigest() != digest:
                        raise OSError("기존 원본의 해시가 일치하지 않습니다")
            finally:
                if temporary is not None:
                    temporary.unlink(missing_ok=True)
        with self.connection:
            self.connection.execute("INSERT OR IGNORE INTO artifacts VALUES (?, ?, ?)", (digest, len(data), str(relative)))
            self.connection.execute("INSERT INTO observations VALUES (?, ?, ?, ?, ?)", (uuid4().hex, run_id, digest, str(path), _now()))

    def report(self, run_id: str) -> dict:
        """실행별 건수와 실패 위치를 반환한다.

        Args:
            run_id: 조회할 수집 실행 ID.
        Returns:
            실행 정보와 실패 목록. 존재하지 않으면 KeyError가 발생한다.
        """
        self.connection.row_factory = sqlite3.Row
        row = self.connection.execute("SELECT * FROM runs WHERE id=?", (run_id,)).fetchone()
        if row is None:
            raise KeyError(run_id)
        result = dict(row)
        result["failures"] = [dict(item) for item in self.connection.execute("SELECT original_path, reason, occurred_at FROM failures WHERE run_id=?", (run_id,))]
        return result
