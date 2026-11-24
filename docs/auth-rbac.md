# 로그인과 부서 기반 자료 분리

상태: 설계 (2026-11-25, `feat/auth-rbac`). 팀 합의 전. 이 문서가 구현의 기준이고, 구현이 다르면 이 문서를 고친다.

## 1. 원칙

1. **신원은 세션에서만 온다.** 요청의 `team`이나 역할 값은 어디에서도 읽지 않는다.
2. **거부가 기본이다.** 새 경로는 따로 열지 않으면 로그인해야 한다. 모르는 부서, 모르는 역할, 깨진 저장소는 막는 쪽으로 실패한다.
3. **판정은 한 곳에서 한다.** `access.visible_clauses` 하나가 "이 계정이 볼 수 있는 조항"을 정하고, 모든 응답은 그 결과만 쓴다. 사후 필터는 없다.
4. **화면은 경계가 아니다.** 화면의 숨김은 편의이고, 서버가 같은 것을 막는다.

## 2. 역할과 범위

| 계정 | 역할 | 조항 범위 | 금액 조항 | 업로드 | 회사 설정 저장 | 계정 관리 |
|---|---|---|---|---|---|---|
| admin | admin | 전체 | 봄 | O | O | O |
| sales | member | 전체 (`receives_all`) | 봄 | O | X | X |
| purchasing, costing | member | 자기 부서 | 봄 | X | X | X |
| development, quality, production_engineering, logistics, legal | member | 자기 부서 | 못 봄 | X | X | X |

- 조항이 범위 안이라는 것: 계정의 부서가 그 조항의 주관 또는 참조 부서이거나, 부서가 `receives_all`이다. 부서 속성은 `data/rules/departments.yaml`에서 읽고 코드에 박지 않는다. 업로드 가능 = admin 또는 `receives_all` 부서.
- 업무 영역 키워드는 보안 판정에 쓰지 않는다.
- 범위 밖이거나 금액을 볼 수 없는 조항은 **자물쇠 행**이 된다: `kind, clause_id, source_doc, section_id`만. 이유는 구분하지 않는다.
- 예외 하나: 금액 열람 부서와 admin의 사전원가(`/precost-setup`, `/precost`, `price-sheet` 경로)는 RFQ 전체를 입력으로 쓰고 그 근거를 보여준다. 부서마다 판정이 달라지지 않게 하기 위해서다. 원문 PDF는 이 예외에 들어가지 않는다.
- 사전원가 답변 기억(`remember`)은 admin만 저장된다. 다른 계정의 `remember: true`는 오류 없이 저장하지 않는다.

## 3. 백엔드 모듈

```python
# autorfx/auth/policy.py
Role = Literal["admin", "member"]
@dataclass(frozen=True)
class Viewer:  user_id: str; username: str; display_name: str; role: Role; department: str | None; must_change_password: bool
@dataclass(frozen=True)
class Scope:   cost_visible: bool; areas: frozenset[str] | None   # None = 전체. 기본값 없음. .is_full, .group
class PolicyError(PermissionError)
def scope_of(viewer, ruleset) -> Scope        # 모르는 부서, 부서 없는 member → PolicyError
def can_upload(viewer, ruleset) -> bool
def can_manage(viewer) -> bool

# autorfx/auth/store.py   (표준 sqlite3, work/_auth/auth.sqlite, 0600, WAL, 쓰기는 BEGIN IMMEDIATE)
class AuthError(Exception): status, code, message
class AuthStore(work_dir, *, department_ids, now=time.time):
    create_user(username, password, *, display_name, role, department, must_change) -> UserRecord
    list_users() / get_user(id) / update_user(id, *, role, department, disabled, display_name)
    set_password(id, password, *, must_change)      # 모든 세션 폐기
    authenticate(username, password, client) -> UserRecord   # 잠금, 더미 해시, 같은 오류 문구
    open_session(user_id) -> token ; viewer_for(token) -> Viewer | None ; close_session(token) ; revoke_sessions(user_id)
    secret() -> bytes                                # 불투명 id용 HMAC 키
# autorfx/auth/passwords.py  scrypt(n=2**15,r=8,p=3,maxmem 명시), 파라미터를 해시와 함께 저장. 8~128자
# autorfx/auth/audit.py      work/_auth/audit.jsonl 추가 전용. 비밀번호·토큰·RFQ 본문·질문 문장은 적지 않는다
# autorfx/auth/seed.py       seed_accounts(store, ruleset, memo_path)
```

- 세션: 토큰 `secrets.token_urlsafe(32)`, 저장은 sha256. 수명 12시간(`AUTORFX_SESSION_HOURS`). 요청마다 계정 행을 다시 읽어 비활성화와 부서 변경이 바로 듣는다.
- 잠금: (아이디, IP) 5회 / IP 전체 30회, 5분. 다른 IP의 정상 로그인은 막지 않는다. 기억하는 키 수에 상한을 둔다. IP는 `request.client.host`만 쓴다.
- 마지막 활성 admin은 삭제·강등·비활성화할 수 없다. 검사는 트랜잭션 안에서 한다.
- 사용자 이름: `[a-z0-9][a-z0-9_-]{2,31}`.

### 게이트 (`api/security.py`)

- 미들웨어(CORS 안쪽): `/health`, `POST /auth/login`, CORS preflight만 열려 있다. 나머지는 유효한 세션이 없으면 본문을 읽기 전에 401 `unauthenticated`.
- `must_change_password`인 계정은 `/auth/me`, `/auth/password`, `/auth/logout` 말고는 403 `password_change_required`.
- GET/HEAD/OPTIONS가 아닌 요청은 `Origin`이 `cors_origin_list`와 정확히 같아야 한다. 없거나 다르면 403 `bad_origin` (로그인, multipart 포함).
- 라우터 의존성 `CurrentViewer`, `AdminViewer`(아니면 403 `forbidden`)를 함께 쓴다.
- 쿠키 `autorfx_session`: HttpOnly, SameSite=Lax, Path=/, Domain 없음, `Secure`는 `AUTORFX_COOKIE_SECURE`(기본 false, HTTPS에서 필수).
- `docs_url=None, redoc_url=None, openapi_url=None`. 모든 응답에 `Cache-Control: no-store`, `Vary: Cookie`. CORS `allow_credentials=True`, 메서드에 PATCH, DELETE 추가.

### 경로

| 경로 | 누가 | 내용 |
|---|---|---|
| `POST /auth/login` | 누구나 | `{username, password}` → 쿠키와 `viewer` |
| `POST /auth/logout` | 로그인 | 세션 폐기, 쿠키 삭제 |
| `GET /auth/me` | 로그인 | `viewer` |
| `POST /auth/password` | 로그인 | `{current_password, new_password}`. 다른 세션 폐기, 새 세션 발급 |
| `GET /admin/users`, `POST /admin/users` | admin | 목록 / 생성(임시 비밀번호를 admin이 정하고 `must_change_password`) |
| `PATCH /admin/users/{id}` | admin | `{display_name?, role?, department?, disabled?}` |
| `POST /admin/users/{id}/password` | admin | 초기화, 세션 폐기, `must_change_password` |
| `POST /admin/users/{id}/logout` | admin | 강제 로그아웃 |
| `DELETE /admin/users/{id}` | admin | 삭제 |
| `GET /admin/audit` | admin | 최근 200건 |
| `GET /rfqs` | 로그인 | `{id, name, status: done\|running\|failed\|stale, created_at, file_count}` 목록 |
| `POST /rfqs` | 업로드 가능 계정 | 지금과 같음, 아니면 403 |
| `PUT /company` | admin | 지금과 같음 |
| `GET /company`, `/precost*`, `price-sheet*` | 금액 열람 | 지금과 같음 |
| 그 밖의 `/rfqs/{id}/...` | 로그인 | 범위 적용 |

없앤다: `/org/departments`, 모든 `team` 쿼리와 본문 값, `org.py`, `meta.teams`, `meta.work_areas`.

`viewer` 모양: `{user_id, username, display_name, role, department, department_label, group, full_scope, can_upload, can_manage, must_change_password}`.

## 4. 범위가 걸리는 곳

`access.visible_clauses(clauses, analyses, scope, ruleset) -> tuple[Clause, ...]`: 분석이 있고, 범위 안이고, 금액 때문에 가려지지 않은 조항. 그중 요약의 인용이 보이는 조항 밖을 가리키면 빼고, 변하지 않을 때까지 되풀이한다.

| 표면 | 규칙 |
|---|---|
| 조항, RTM, RTM Excel | 보이지 않는 조항은 자물쇠 행. `department` 보기 필터는 보이는 조항에만 작용하고 자물쇠 행은 그대로 남는다. Excel에는 어떤 범위로 만들었는지 적는다 |
| 제목 | `own_title`이거나 그 조항의 본문에 나올 때만. 물려받은 상위 절 제목은 전체 범위가 아니면 빈 문자열. 금액 제목 규칙은 그대로 |
| 문장 | 보이는 조항의 문장만(allowlist). 전체 범위만 전부 |
| 원문 PDF | 전체 범위는 기준 PDF, 그 밖은 §5 |
| 정합성 | 전체 범위는 전부. 아니면 증거가 모두 보이는 조항 안에 있고 `len(evidence) < MAX_EVIDENCE`일 때만 보이고, 나머지는 자물쇠. 자물쇠는 뒤로 모은다. `is_cost_sensitive`와 금액 규칙 유지 |
| 검색·챗봇 | `SearchIndex.scoped(ids)`, `DenseIndex.subset(ids)`. BM25 통계·`missing`·제안 질문 모두 범위 안 말뭉치로. LLM 근거 키는 보이는 키의 부분집합이 아니면 `no_evidence` |
| 평가 | 오류 행은 보이는 조항·이슈 id만. 지표는 전체 집계 |
| 문서 정보 | 보이는 조항이 하나도 없는 파일은 이름 `파일 {id}`, 경고 없음, 조항 수 0. 패키지 이름은 첫 파일이 보일 때만, 아니면 `RFQ {id 뒤 6자}` |
| 작업 진행 | 전체 범위가 아니면 단계 이름과 상태만, `detail`은 빈 문자열. 409 `job_failed` 메시지도 단계 이름까지만(`detail`에 파일 이름이 있다) |
| RFQ 목록 | 이름 규칙은 문서 정보와 같다. 결과를 읽을 수 없으면 전체 범위가 아닌 계정에는 `RFQ …` |
| 불투명 id | `sha256` 대신 `AuthStore.secret()`의 HMAC |

`hidden_keys`, `build_masked_pdf`, `pipeline.py`, `data/rules/`, `PIPELINE_VERSION`은 바꾸지 않는다. CLI 도구(`compare.py`, `score.py`)는 전체 범위로 돈다.

## 5. 범위용 원문 PDF (`scoped_pdf.py`)

요청 때 기준 PDF에서 만든다(`masked.pdf`에서 시작하지 않는다). 글자 가림만으로는 `/ActualText`와 cropbox 밖 글자가 남으므로 평탄화한다.

1. `allowed` = 보이는 문장의 키. `hidden` = 나머지 모든 파싱 문장(어느 조항에도 없는 제목 포함).
2. 쪽 수나 위치가 기준 PDF와 맞지 않으면 거부.
3. 메모리에서 쪽마다 주석·위젯·링크 제거. 허용 상자와 숨김 상자(`TRIM` 적용)를 만든다. 단어는 중심이 허용 상자 안이고 숨김 상자 밖일 때만 남기고 나머지는 redact(`images=1, graphics=2`).
4. `_assert_removed(canonical, temp, hidden)`과, 쪽마다 남은 단어가 남기기로 한 단어의 다중집합 부분집합인지 확인.
5. 새 문서: 같은 쪽 크기, 회색 바탕, 허용 상자만 `get_pixmap(clip, dpi=144, annots=False)` 이미지로. 메타데이터·목차·글꼴 없음.
6. 다시 열어 쪽 수, 빈 텍스트, 글꼴·주석·위젯·첨부 없음, 이미지 수, 허용 상자 밖 픽셀이 회색인지 확인.
7. 예외나 불일치면 임시 파일을 지우고 `AccessDenied`(403). 아니면 `os.replace`.

캐시: `work/<rfq>/scoped/<sha256(SCOPE_VERSION, canonical_sha256, 정렬한 허용 키, 문장 상자)>.pdf`, 키별 락, 고유 임시 이름.

## 6. 웹

- API 주소는 `http://localhost:8000`처럼 **`localhost`** 로 쓴다(`127.0.0.1`은 다른 사이트라 쿠키가 가지 않는다). 모든 `fetch`에 `credentials: "include"`.
- `AuthGate`: `/auth/me` 확인 중에는 아무것도 그리지 않고, 없으면 로그인 창, `must_change_password`면 비밀번호 변경 창, 있으면 앱.
- 상단: 계정 이름·부서·금액 열람 여부·로그아웃. 팀 선택과 부서 설정은 없다. admin에게만 `/admin`(계정 표, 생성, 부서·역할 변경, 초기화, 강제 로그아웃, 비활성화, 감사 기록).
- 홈: RFQ 목록. 업로드 상자는 `can_upload`일 때만.
- 자물쇠 행 문구: "열람 권한이 없는 조항입니다".
- 401이면 로그인 창으로, 다른 탭의 로그아웃도 반영(`BroadcastChannel`, 포커스 때 `/auth/me`). 리소스 키는 `user_id`로 하고 계정이 바뀌면 화면 상태를 버린다.

## 7. 계정 만들기

`uv run autorfx users seed`: 없는 계정만 만든다(admin + 룰셋 부서마다 하나, 아이디 = 부서 id, 시드 계정은 `must_change_password=false`). 무작위 비밀번호를 `ACCOUNTS.local.md`(git 제외, 0600)에 쓴다. 이미 있는 계정은 건드리지 않고, 메모가 있으면 새 계정만 덧붙인다. 메모에 쓰지 못하면 계정을 만들지 않는다. `users list`, `users reset <아이디>`도 있다.

## 8. 남는 한계

- 시트 하나가 조항 하나라 참조 부서 하나면 시트 전체가 보인다.
- 조항 번호는 나간다. FAR형 번호는 번호만으로 내용을 짐작할 수 있다.
- 허용 상자 안의 그림 글자는 남는다. 상자 밖 그림은 회색이 된다.
- 부서 분류가 틀리면 범위도 틀린다. 부서 키워드는 도메인 팀 확인 전 가정값이다.
- 올린 RFQ 단위의 접근 제어는 없다. 같은 패키지를 다시 올리면 같은 RFQ가 된다.
- 감사 기록은 서명되지 않는다. 프로세스 하나를 가정한 잠금은 재시작하면 비워진다.
