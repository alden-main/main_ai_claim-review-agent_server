# 심사청구 Agent 서버

공공 기준 데이터를 준비하는 수집·전처리 영역과 청구 건을 검토하는 Agent 영역을 하나의 Python 프로젝트에서 구분한다. 현재는 수동 원본 수집과 필드 변환 기반을 구현한 초기 단계다.

## 프로젝트 구조

```text
src/main_ai_claim_review_agent_server/
├── ingestion/   # 수가·기준 문서 수집, 원본 보관, 정규화·품질 검증
└── agent/       # Claim Context, 규칙 검증, 근거 검색, 심사팀 검토

docs/            # 요구사항·설계·구현 계획
```

두 영역은 향후 각각 독립적으로 실행·설정할 수 있도록 구현한다. 수집 영역에는 수동 파일 수집 명령과 PostgreSQL 이력 저장 기능이 있으며, Agent 실행 기능은 아직 구현되지 않았다. 기존 기본 실행 명령도 실제 Agent 서버가 아니다.

수집 영역은 원본과 출처·버전을 보관하고 정규화 및 품질 검증 결과를 기록한다. Agent는 검증된 수가 데이터와 심사팀이 승인한 규칙·기준 문서 버전을 참조하며, 청구 시점의 환자별 Context를 구성한다. 규칙은 수가 마스터로부터 자동 생성되지 않는다. 초기에는 수집이 우선이며, 기준 문서의 청킹·임베딩은 RAG 구현 단계에서 진행한다.

상세 내용은 [요구사항 정의서](docs/01-요구사항-정의서.md), [소프트웨어 설계](docs/02-소프트웨어-설계.md), [구현 계획](docs/03-구현-계획.md)을 참고한다.

데이터 수집·전처리 상세 문서: [요구사항](docs/data/01-데이터-처리-요구사항.md) · [설계](docs/data/02-데이터-처리-설계.md) · [구현 계획](docs/data/03-데이터-처리-구현-계획.md).

## 수동 원본 수집 시작 기능

수동 확보한 파일을 로컬 저장소에 등록할 수 있다. 저장 경로는 원본 자료의 이용 조건과 접근 정책에 맞는 위치를 지정한다.

```bash
python -m main_ai_claim_review_agent_server.ingestion --store /path/to/store collect \
  --source SRC-02 --scope '수동 확보한 시범 파일' /path/to/file
python -m main_ai_claim_review_agent_server.ingestion --store /path/to/store report 실행ID
```

원본은 SHA-256별로 보존하고 같은 내용의 재수집도 별도 관측으로 기록한다. 부분 실패 시 보고서의 실패 경로를 확인해 해당 파일을 다시 수집할 수 있다. 완료는 요청한 파일의 처리 완료를 의미하며 원천 전체성은 미확인으로 기록된다. `ingestion.normalize`는 명시적인 필드 매핑과 원본 위치를 받아 문자열·십진수 변환 및 결측·오류를 기록하는 초기 기능이다. 결과는 검수 대기 상태이며 발행되지 않는다.

API의 실제 응답 검증, XLSX 업무 의미 검증, 문서 관계, 검수 이력 및 발행은 아직 완료되지 않았다. 실제 원천 명세와 이용 조건 확인이 필요하다. 로컬 저장소에는 별도의 암호화·접근 제어가 없으므로 운영 환경에 바로 사용하지 않는다.

### 행정규칙 본문 조회

국가법령정보 공동활용 신청·이용 조건을 확인하고 발급받은 인증값을 환경변수 `LAW_API_OC`에 설정한다. 인증값은 명령 인자나 저장소에 기록하지 않는다.

```bash
python -m main_ai_claim_review_agent_server.ingestion law-fetch 2100000281466 /path/to/new-response.xml
python -m main_ai_claim_review_agent_server.ingestion --store /path/to/store collect \
  --source SRC-03 --scope '행정규칙 일련번호 2100000281466' /path/to/new-response.xml
```

첫 명령은 응답의 일련번호와 제목을 검증한 뒤 새 파일에 원본 XML을 저장한다. 두 번째 명령으로 원본을 등록한다. 문서 관계와 조항별 적용 범위는 미확인 상태이며, 실제 API 응답에 대한 검증은 인증 후 필요하다. 시범 수가파일의 출처·이용 조건 및 법령 API 확인 내용은 [원천 확인 기록](docs/data/04-출처-확인-및-법령-api.md)을 참고한다.

### 약국수가 API 페이지 조회

[공공데이터포털 수가기준정보조회서비스](https://www.data.go.kr/data/15021028/openapi.do)의 활용신청 및 이용 조건을 확인한 후, 발급받은 **디코딩 인증키**를 환경변수 `HIRA_SERVICE_KEY`로 설정한다. 키를 명령 인자나 저장소에 기록하지 않는다.

```bash
python -m main_ai_claim_review_agent_server.ingestion fee-pharmacy-fetch /path/to/new-page.xml --page 1 --page-size 10 --code Z3000030
python -m main_ai_claim_review_agent_server.ingestion --store /path/to/store collect \
  --source SRC-01 --scope '약국수가 코드 Z3000030, 1페이지' /path/to/new-page.xml
```

`--category`, `--name`으로도 검색할 수 있다. 한 번에 한 페이지만 조회하고, 출력한 요청 조건과 건수를 기록한 뒤 원본 파일을 별도로 등록한다. API 응답 구조와 성공 코드, 검색 조건 없는 전체 조회, 실제 허용 페이지 크기는 실응답으로 검증되지 않았다. 전체성은 항상 미확인으로 표시한다. 진료·한방 기능, 자동 페이지 순회·재시도, 실패 응답 보관과 재개, 자동 정규화는 아직 구현되지 않았다.

### 시범 수가 XLSX 추출

```bash
python -m main_ai_claim_review_agent_server.ingestion fee-xlsx-extract \
  /path/to/수가전체판.xlsx /path/to/new-fees.jsonl.gz > /path/to/report.json
```

시범 파일의 7개 시트와 첫 행 헤더를 대조하고 원본 셀의 주소·유형·스타일 ID·값과 수식을 압축 JSON Lines로 추출한다. 각 레코드에는 원본 SHA-256, 시트·행, 매핑 버전과 추출·검토 상태가 포함된다. 숫자는 XLSX의 원본 문자열로 보존하며 날짜·금액·0의 의미는 해석하지 않는다. 숫자로 저장된 수가코드나 수식·오류 셀이 있는 행은 검토 대상으로 표시한다. 보고서의 `headers` 순서와 각 셀 주소로 원본 열을 확인할 수 있다. 전체성·품질 및 이용 조건은 미확인이고 발행하지 않으며 별도 명령으로 PostgreSQL에 적재할 수 있다. 지원 대상은 조사한 샘플 구조에 한정하며 다른 시트·헤더는 실패 처리한다.

### PostgreSQL 저장

PostgreSQL 서버를 준비하고 패키지 의존성을 설치한 뒤 `DATABASE_URL` 환경변수를 설정한다. 접속 정보는 저장소에 기록하지 않는다. `--store`에는 원본 파일 저장 경로를 지정하며, 같은 데이터베이스를 사용할 때 동일한 경로를 유지해야 한다. 최초 연결 시 테이블을 생성한다.

```bash
python -m main_ai_claim_review_agent_server.ingestion --store /path/to/store collect \
  --source SRC-02 --scope '수동 확보 샘플, 실제 입수 경로 미확인' /path/to/수가전체판.xlsx
python -m main_ai_claim_review_agent_server.ingestion --store /path/to/store fee-xlsx-load \
  /path/to/new-fees.jsonl.gz /path/to/report.json
```

이력과 추출 행은 PostgreSQL에 저장하고 원본 바이트는 지정 디렉터리에 보존한다. XLSX 원본을 먼저 등록해야 하며, 적재 시 원본 해시·매핑 버전·보고서 건수를 대조한다. 재실행 시 같은 원본·시트·행·매핑 버전은 중복 저장하지 않는다. 검토 대상 행도 상태를 보존하며 전체성·품질은 미확인이다.

API 출처 `SRC-01`은 건강보험심사평가원 / 공공데이터포털로 기록한다. XLSX의 `SRC-02`는 심평원 자료실이며, 시범 원본과 사용자가 해당 게시물에서 내려받았다고 제공한 파일의 해시 일치 이력은 `artifact_source_evidence`에 기록한다. 이 확인은 이용 조건의 승인이나 출처 범주의 모든 파일 확인을 뜻하지 않는다. 법령 자료는 별도 출처다. 기존 SQLite 카탈로그는 자동 이전하지 않으며 원본 파일과 함께 보존해야 한다. PostgreSQL 연결 및 적재 성공 여부는 실제 서버에서 확인해야 한다. 벡터 색인은 후속 RAG 단계다.

### Docker를 이용한 로컬 PostgreSQL

`compose.yaml`은 PostgreSQL 16과 pgvector를 로컬에서 실행한다. DB 이름은 `claim_review`, 초기 관리자 계정은 `main`, 호스트 기본 포트는 `55432`다. 최초 사용 시 `.env.example`을 `.env`로 복사하고 `POSTGRES_PASSWORD`를 원하는 영문·숫자 비밀번호로 설정한다. `.env`는 Git에 포함하지 않는다. 포트 충돌 시 초기화 전후 모두 `POSTGRES_PORT`를 변경할 수 있다.

```bash
cp .env.example .env  # .env가 없을 때만 실행하고 비밀번호 수정
# .env가 이미 있다면 덮어쓰지 않는다.
docker compose up -d
```

현재 Python 코드는 `.env`를 자동으로 읽지 않는다. 프로젝트 디렉터리에서 아래 명령을 실행한 터미널에서 수집·적재 명령을 실행한다.

```bash
set -a
source .env
set +a
export DATABASE_URL="postgresql://main:${POSTGRES_PASSWORD}@127.0.0.1:${POSTGRES_PORT:-55432}/claim_review"
python -m main_ai_claim_review_agent_server.ingestion --store /path/to/store collect \
  --source SRC-02 --scope '수동 확보 샘플, 입수 경로 미확인' /path/to/수가전체판.xlsx
```

위 URL 구성 예시는 영문·숫자 비밀번호를 전제로 한다. URL 특수문자를 비밀번호로 사용하면 URL 인코딩이 필요하다. 애플리케이션을 같은 Compose 네트워크의 컨테이너로 실행할 때는 `127.0.0.1:${POSTGRES_PORT}` 대신 `db:5432`를 사용한다.

데이터는 Docker named volume에 보존된다. `docker compose down`은 컨테이너를 정지·삭제하지만 볼륨을 유지한다. `docker compose down -v`는 DB 데이터도 삭제한다. 원본 파일은 `--store` 디렉터리에 별도로 보관하므로 DB 볼륨과 함께 백업해야 한다. pgvector 확장은 빈 DB의 최초 초기화 시 활성화되며 임베딩 데이터는 자동 생성하지 않는다.

`POSTGRES_PASSWORD`는 빈 볼륨의 최초 초기화에만 적용된다. 기존 DB의 비밀번호를 변경하려면 `docker compose exec db psql -U main -d claim_review`에서 `\password main`을 실행해 새 비밀번호를 입력하고, `.env`의 값도 같게 수정한 뒤 터미널 환경변수와 `DATABASE_URL`을 다시 설정한다. `.env`만 수정하면 기존 DB 비밀번호는 바뀌지 않는다. `main`은 개발용 관리자 계정이므로 운영 환경에서는 별도 권한 계정을 사용한다.

### 수가코드·명칭 검색

원본 XLSX 등록과 추출 행 적재를 완료한 후 아래 명령으로 검색용 코드·명칭을 정규화한다. 앞의 Docker 안내에 따라 `DATABASE_URL`을 설정해야 한다.

```bash
python -m main_ai_claim_review_agent_server.ingestion --store .local-data fee-normalize \
  04b46c50f5c045ac35719fb0d8a0247e2932f3320ff1446eabc564ad58319edc
python -m main_ai_claim_review_agent_server.ingestion --store .local-data fee-search --code 001
python -m main_ai_claim_review_agent_server.ingestion --store .local-data fee-search --name 진찰료 --limit 10
```

코드는 문자열로 정확히 일치시키고, 명칭은 입력 문자열을 포함하는 항목을 검색한다. `--artifact 원본SHA256`으로 버전을 제한할 수 있으며 생략 시 모든 원본에서 조회한다. 결과에는 원본 해시·시트·행·매핑 및 정규화 버전과 함께 검토 상태가 제공된다. 결과가 제한을 초과하면 `has_more`가 `true`가 되며, `next_offset`을 다음 요청의 `--offset`에 지정할 수 있다. 동일 코드의 여러 항목은 합치지 않는다. 원본에서 코드·명칭을 안전하게 확인하지 못한 행은 검토 상태로 기록하고 검색에서 제외한다.

이 기능은 검증된 시범 파일의 시트·헤더 구조만 처리한다. 데이터의 업무 품질·적용기간·사용 승인은 확인되지 않았으며, 검색 결과를 진료일에 자동 적용하거나 금액 계산에 사용하지 않는다. 명칭 부분 검색은 PostgreSQL `pg_trgm` 확장과 인덱스를 사용한다. 최초 정규화 시 확장 설치 권한이 필요하다. 새 원본 버전이 적재되면 해당 해시로 정규화 명령을 다시 실행한다.

파일 기반 수가·약제·치료재료 갱신 경로와 시행 근거는 [파일 기반 갱신 원천목록](docs/data/05-파일기반-갱신-원천목록.md)을 참고한다.
