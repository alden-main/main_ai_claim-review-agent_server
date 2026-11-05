# 심사청구 Agent 서버

공공 기준 데이터를 준비하는 수집·전처리 영역과 청구 건을 검토하는 Agent 영역을 하나의 Python 프로젝트에서 구분한다. 현재는 수동 원본 수집과 필드 변환 기반을 구현한 초기 단계다.

## 프로젝트 구조

```text
src/main_ai_claim_review_agent_server/
├── ingestion/   # 수가·기준 문서 수집, 원본 보관, 정규화·품질 검증
└── agent/       # Claim Context, 규칙 검증, 근거 검색, 심사팀 검토

docs/            # 요구사항·설계·구현 계획
```

두 영역은 향후 각각 독립적으로 실행·설정할 수 있도록 구현한다. 수집 영역에는 로컬 수동 파일 수집 명령과 SQLite 이력 저장 기능이 있으며, Agent 실행 기능은 아직 구현되지 않았다. 기존 기본 실행 명령도 실제 Agent 서버가 아니다.

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

공식 API 수집, XLSX 시트별 파싱 및 매핑, 문서 관계, 영구적인 전처리 결과·검수 이력, 발행은 아직 구현되지 않았다. 실제 원천 명세와 이용 조건 확인이 필요하다. 로컬 저장소에는 별도의 암호화·접근 제어가 없으므로 운영 환경에 바로 사용하지 않는다.

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
