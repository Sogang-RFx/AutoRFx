# AutoRFx

> 자동차 부품 협력사(Tier-1/Tier-2)를 위한 다국어 RFQ 분석 · 정밀 Citation 추적 · 사전원가 판단 플랫폼  
> **서강대학교 빅데이터종합설계(캡스톤디자인) 3조 × (주)겁쟁이사자들**

RFQ 한 건(PDF, DOC, DOCX, XLSX로 된 파일 묶음, 영어·독일어)을 조항 단위로 나눠 부서별로 전달하고, 모든 분석 결과를 원문의 정확한 위치로 되짚게 합니다.

---

## 지금 되는 것 (2026-11-25, `feat/auth-rbac`)

백엔드는 **LLM 키 없이 규칙만으로** 접수부터 API까지 끝까지 동작합니다. 개발용 RFQ 3건(1개 파일 26쪽, 6개 파일 107쪽, 6개 파일 7쪽)이 사람 손 없이 처리됩니다.

| 부분 | 상태 | 계산 방법 |
|---|---|---|
| 로그인·계정 관리·감사 기록 | 작동 | 세션 쿠키(`HttpOnly`, `SameSite=Lax`), `scrypt` 비밀번호, 관리자 화면(`/admin`), CLI(`autorfx users seed/list/reset`). 상세는 `docs/auth-rbac.md` |
| 패키지 접수, 원본 보관, 기준 PDF | 작동 | 결정적. `.doc`는 LibreOffice 또는 macOS가 필요. 업로드는 `admin`과 전체 열람 부서(`sales`)만 가능 |
| 문장·조항 파싱, 시트의 셀 주소 | 작동 | 결정적. PDF 안의 표는 아직 문장으로 읽음 |
| 부서 분류(주관·참조), 요건 강도, 독소조항 | 작동 | 룰셋(`data/rules/`). LLM은 연결됐을 때 애매한 조항만 확인 |
| 인용 검증 | 작동 | 결정적. 모든 결과가 원문 위치와 대조됨 |
| 문서 간 정합성 (값 충돌, 첨부 누락, 참조 오류, 표기 불일치) | 작동 | 결정적. 제한 계정에는 보이는 조항 안의 근거로만 이뤄진 이슈만 공개 |
| 부서 기반 자료 분리 & 범위 PDF | 작동 (한계 있음) | `access.visible_clauses` 한 곳에서 계정별 열람 범위를 결정하고 조항·RTM·정합성·검색·챗봇·원문 PDF·Excel에 적용. 제한 계정의 원문 PDF는 허용 상자만 평탄화 이미지로 생성(`scoped_pdf.py`). **허용 상자 안의 그림 글자는 가리지 못함** |
| 검색·질의응답 | 작동 | 계정별 열람 범위의 `ScopedSearchIndex`(BM25+용어 사전, `OPENAI_API_KEY`가 있으면 OpenAI `text-embedding-3-large` 하이브리드)로만 검색. 문장별 인용·숫자 검증, 대화 기록, 저장된 독소조항·정합성 질의 지원(`src/autorfx/chat/`) |
| 사전원가 Go/No-Go (유형 분류, 1차 넉아웃 Q&A, 원가 항목, 수명주기 최소견적, 충격 범위, 가중 점수) | 작동 | `Decimal` 계산. 금액 열람 부서와 `admin`만 접근 가능, 회사 설정(`/settings/company`) 저장과 답변 기억은 `admin`만. 점수 가중치와 충격 크기는 `data/rules/cost_model.yaml`의 가정값 |
| RTM Excel 내보내기 | 작동 | 서버에서 계정의 열람 범위로 생성 |
| 정답지 채점 | 작동 | 정답지가 없으면 수치를 내지 않음. 제한 계정에는 보이는 조항·이슈의 오류 행만 표시 |
| 조항 요약, OpenAI 문장별 번역, 생성형 답변 | 작동 (품질·비용은 도메인 검수 전) | LLM 경로. `OPENAI_API_KEY` 연결 시 동작, 없으면 규칙 기반 유지 |
| 임베딩 검색 (3-large 기본) | 작동 (정답표는 검수 전) | 11/24 정답표(질문 30 × RFQ 5, 123칸)에서 하이브리드 3-large 82/123, BGE-M3 71/123. 근거 기준은 모델별 측정값(3-large 0.30, BGE-M3 0.55)을 자동 적용 |
| 과거 제안서 DB, 전용 번역 API(DeepL), 외부 배포 | 없음 | — |

룰셋의 부서 키워드, 금액 열람 팀, 판정 규칙은 개발용 문서에서 뽑은 **가정값**입니다. 화면 스택(Next.js)과 백엔드 방식(FastAPI)은 팀 합의 전입니다.

---

## 빠른 시작

### 백엔드

```bash
cp .env.example .env            # 키가 없어도 됩니다
uv sync
uv run pytest                   # 키와 네트워크 없이 통과합니다

uv run autorfx samples          # data/raw/dev/ 아래의 개발용 RFQ 목록 (저장소에는 없음)
uv run autorfx analyze --sample <id>
uv run autorfx analyze path/to/rfq.pdf path/to/annex.xlsx

uv run autorfx users seed       # 관리자 + 부서별 기본 계정 생성 (비밀번호는 ACCOUNTS.local.md에 저장, git 제외)
uv run autorfx users list       # 계정 목록 확인
uv run autorfx users reset <id> # 비밀번호 초기화

uv run autorfx serve --port 8000
```

### 임베딩 검색

기본은 OpenAI `text-embedding-3-large`입니다 (`.env`의 `OPENAI_API_KEY`만 있으면 됨, 모델 내려받기 없음). 키가 없으면 키워드 검색만 씁니다. 근거 기준(`EMBEDDING_MIN_SCORE`)은 비워 두면 모델별 측정값을 씁니다. 모델마다 점수 눈금이 달라서 3-large는 0.30, BGE-M3는 0.55입니다.

```bash
# .env: EMBEDDING_MODEL=                         # 비우면 임베딩을 끄고 키워드 검색만
# .env: EMBEDDING_MODEL=BAAI/bge-m3              # 로컬 모델로 바꾸기 (첫 실행 때 약 2.3GB)
uv sync --extra embedding                        # BGE-M3를 쓸 때만: sentence-transformers + torch
uv run autorfx compare-search --sample <id>      # 질문으로 BM25 · 임베딩 · 하이브리드 비교
uv run autorfx compare-search --sample <id> --model BAAI/bge-m3   # 다른 모델로 비교
```

질문은 `data/questions/retrieval_ko.yaml`에 있습니다. 질문별 정답 표(`data/answer_keys/retrieval_ko.yaml`, 커밋하지 않음)가 있으면 사람이 O/X를 매기지 않고 자동으로 채점합니다:

```bash
uv run autorfx score-search --gates 0.2,0.3,0.4,0.5,0.6   # BM25 · 임베딩 · 하이브리드 × 근거 기준 (모델은 EMBEDDING_MODEL, 바꾸려면 --model)
```

`compare-search`의 결과(`.md`, 채점용 `.csv`)는 `data/work/_compare/`에 생기며 RFQ 원문이 들어 있으니 커밋하지 않습니다. `score-search`는 원문 없이 점수표 `data/work/_compare/score-*.md` 하나만 만듭니다. 정답 표에 있는 RFQ가 `data/raw/dev`에 하나라도 없으면 멈추고(일부만 보려면 `--allow-missing`), 분석은 API 키가 있어도 규칙으로만 해서 LLM을 부르지 않습니다.

개발용 RFQ는 저장소에 없습니다. 팀 공유 폴더에서 받아 `data/raw/dev/<RFQ 이름>/`에 폴더째 둡니다. 평가용 세트는 개발에 쓰지 않으며, 코드가 `eval`로 시작하는 경로를 읽지 않습니다.

### 웹

```bash
cd web
pnpm install
# 백엔드(autorfx serve)가 떠 있어야 합니다. 세션 쿠키가 오가려면 같은 사이트(localhost)로 맞춥니다.
NEXT_PUBLIC_RFQ_API_BASE=http://localhost:8000 pnpm dev   # http://localhost:3000

pnpm test                       # 단위·계약 테스트
RFQ_E2E_FILES="/path/a.pdf:/path/b.xlsx" pnpm test:e2e     # 실제 백엔드로 시연 흐름 (변수가 없으면 건너뜀)
pnpm contract:emit              # zod 계약 → /contract/*.schema.json
```

웹은 실제 백엔드로만 동작합니다. 백엔드 주소가 없거나 응답이 없으면 다른 데이터로 채우지 않고 오류를 보여줍니다. 저장소에는 RFQ 파일, 샘플, 화면 캡처를 두지 않습니다. 시연과 테스트에는 직접 올린 파일만 씁니다.

---

## 핵심 설계 원칙

1. **원본 불변**: RFQ 원문의 텍스트·표·서식을 바꾸지 않고, 파일·페이지·문장 위치를 끝까지 보존합니다.
2. **검증된 인용만**: 결과를 누르면 원문의 정확한 위치(예: `30페이지 3번째 문장`)가 열립니다. 인용은 LLM 없이 원문과 대조해 검증합니다.
3. **규칙이 먼저, LLM은 확인**: 판정 기준은 도메인 팀이 고치는 룰셋 파일에 있습니다. LLM이 없어도 끝까지 동작하고, 결과가 무엇으로 계산됐는지를 화면에 밝힙니다.
4. **문서 간 정합성**: 파일마다 다르게 적힌 값, 받지 못한 첨부, 없는 조항을 가리키는 참조를 찾아 근거와 함께 보여줍니다.
5. **부서별 권한 분리**: 금액 조항은 볼 수 없는 팀의 응답, 원문 PDF, 챗봇, 내보낸 파일 어디에도 나가지 않습니다. 화면에서만 가리지 않습니다.
6. **결정적 사전원가**: 원가와 Go/No-Go는 코드가 계산합니다. 회사 단가는 프로그램에 직접 넣고, 모르는 단가는 0으로 대신하지 않고 계산을 멈춥니다. 충돌이 풀리지 않아 경우가 갈리면 판정을 보류합니다.

---

## 저장소 구조

```text
AutoRFx/
├── contract/            # 화면과 백엔드가 공유하는 JSON Schema (web/src/lib/rfq/contract.ts에서 생성)
├── data/
│   └── rules/           # 도메인 팀이 관리하는 룰셋 (부서, 판정 표현, 독소조항, 정합성 항목, 용어 사전, 금액 민감, 판정 규칙)
├── docs/                # 아키텍처
├── src/autorfx/         # Python 백엔드
│   ├── ingest/          # 접수, 원본 보관, 기준 PDF 변환
│   ├── parser/          # 문장, 좌표, 조항 계층
│   ├── chunker/         # 조항 청크와 금액 민감도
│   ├── analysis/        # 규칙 후보, LLM 제공자, 문장별 번역, 인용 검증
│   ├── auth/            # 계정 저장소, 비밀번호(scrypt), 세션, 정책(Scope), 감사 기록, 시드
│   ├── chat/            # 범위 기반 챗봇(계획, 근거 수집, 문장별 인용·숫자 검증)
│   ├── consistency/     # 문서 간 정합성
│   ├── retrieval/       # BM25+임베딩 색인(ScopedSearchIndex) 및 검색 비교
│   ├── precost/         # 사전원가와 판정, 가격표 읽기
│   ├── access.py        # 열람 범위 판정(visible_clauses)과 응답 필터
│   ├── scoped_pdf.py    # 제한 계정용 평탄화 원문 PDF
│   ├── pipeline.py      # 접수부터 저장까지
│   ├── api/             # FastAPI (보안 게이트, 인증·관리자·RFQ 라우트)
│   └── cli.py           # autorfx 명령
├── tests/               # 지어낸 자료로 하는 백엔드 테스트
├── web/                 # Next.js 화면
├── .env.example
└── pyproject.toml
```

고객 원본 문서(`data/raw/`), 처리 결과(`data/work/`), 정답지는 저장소에 올리지 않습니다.

---

## 팀 & R&R

자세한 팀원 역할 및 조직 소개는 [Sogang-RFx 프로필 페이지](https://github.com/Sogang-RFx)에서 확인할 수 있습니다.
