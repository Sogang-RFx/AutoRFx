# 몰입 계층 설계 — 잉크 입자, 인트로, 3D 흐름도, 챗봇 스트리밍과 음성

**지위**: 브랜치 `feat/immersive-ui`의 설계 정본이다. Tech Lead의 2026-11-25 요청으로 시작했고 팀 합의 전이다. DESIGN.md §1은 입자 배경을 권장한다(2026-11-25 Tech Lead 지시로 §10에 있던 금지를 지웠다). 머지할 때 DESIGN.md §3(화면 목록)과 SPEC의 챗봇 절을 이 문서에 맞춰 고친다.

**범위**: 화면의 입자·흐름 효과, 첫 화면 인트로, RFQ 흐름도(3D), 챗봇 화면 재구성, 챗봇 진행 스트리밍, 음성 질문과 답 읽기.

**참고**: 로컬에 보관한 이전 프로젝트와 강의 노트에서 구조와 방식만 가져왔다. 코드와 자산은 옮기지 않고 이 저장소의 토큰과 규칙으로 새로 쓴다.

---

## 1. 방향: 바탕에는 입자장, 옮겨질 때는 흐름

AutoRFx의 시각 언어는 "원문과 해석의 구분"이다. 입자는 그 사이에 있는 것을 그린다. 페이지 바탕에는 늘 입자장이 있고, RFQ의 문장이 원문에서 떨어져 나와 조항이 되고, 부서로 가고, 검증을 거쳐 답이 될 때 그 점들이 흘러 글자로 자리를 잡는다.

- 종이 위의 잉크다. 점은 `--color-ink`가 대부분이고 일부가 `--color-ink-muted`다. `--color-accent`는 선택과 흐름의 강조에, `--color-danger`는 실제 오류 상태에만 쓴다. 새 색은 없다.
- 점은 1~2px의 또렷한 네모다.
- 챗봇에서는 이 규칙이 제품의 원칙과 겹친다. **검증 전에는 점이고, 검증을 통과하면 문장이다.**

## 2. 규칙

DESIGN.md §1에 따라 입자 배경을 권장한다.

1. **바탕에 입자장을 둔다.** 모든 화면의 페이지 바탕에 점이 잔잔히 흐른다(§4.6). 패널과 원문은 불투명한 면이라 그 위에 놓이고, 점은 그 사이와 둘레에서 보인다.
2. **옮겨질 때 점이 흐른다.** 인트로에서 첫 화면으로, 화면에서 화면으로, 질문에서 답으로, 올린 파일에서 처리 단계로.
3. **글자는 늘 DOM에 있다.** 캔버스는 `aria-hidden`이고 `pointer-events: none`이다. 점이 날아오는 동안 글자는 `opacity: 0`으로 자리를 지키다가 겹쳐서 나타난다.
4. **끌 수 있다.** `prefers-reduced-motion`이면 입자와 인트로가 없고 내용이 바로 보인다. 계정 메뉴에 "화면 효과" 스위치가 있고 `localStorage`에 남는다. 꺼도 기능은 같다.
5. **진행을 나타내는 점은 실제 값만 따른다.** 처리 단계, 작성된 문장 수, 마이크 입력 크기처럼 진행을 보여 주는 점은 서버와 장치가 준 값에서만 나온다. 바탕의 입자장은 진행을 나타내지 않는다.
6. **가볍다.** 2D 입자는 Canvas 2D와 타입 배열로 그리고 라이브러리를 쓰지 않는다. three.js는 흐름도 화면에서만 따로 불러온다(처음 내려받는 스크립트 300KB 목표를 지킨다).

움직임 값은 DESIGN §4.3에 더한다: 점의 비행 520~900ms(지터 최대 180ms), 글자 겹침 460ms, 덮개 걷힘 480ms. 화면 요소의 색·불투명도 180ms와 패널 280ms는 그대로다.

## 3. 구조

```text
web/src/lib/fx/            React와 three.js 없는 TypeScript. 계산은 node에서 단위 테스트한다
  tone.ts                  점의 색 종류(ink, muted, accent, danger)와 읽을 CSS 토큰 이름
  motion.ts                비행 곡선, 완화, 밀어내기, 시드 난수. 시간 상수(CSS 토큰과 같은 값)
  prefs.ts                 효과 켬·끔 저장소, 줄인 움직임, 인트로 다시 보기 채널
  first-load.ts            하드 로드의 첫 커밋은 건너뛰는 판단(shouldRevealOnMount)
  sample.ts                글자·요소·도형을 점 좌표로 바꾸기, 점 줄이기(thin)
  canvas-kit.ts            DPR 상한, 프레임 루프(숨김·화면 밖이면 멈춤), 포인터, 토큰 색 읽기
  flow.ts                  관문(gate)을 지나는 점의 계획과 이동(순수)
  particles.ts             ParticleLayer: 캔버스 한 장의 점 층(타입 배열)
  sphere.ts                ParticleSphere: 구 격자, 투영, 물결, 음성 크기 입력
  reveal.ts                대상 요소를 점에서 글자로 바꾸는 제어기(DOM, React 없음)
  ambient.ts               바탕 입자장의 계산(순수): 점의 수, 흐름장, 한 걸음, 프레임을 그릴지
  ambient-layer.ts         AmbientField: 바탕 입자장의 캔버스(프레임 루프, 그리기)
web/src/components/fx/     캔버스 한 장과 React 연결
  ambient-field.tsx        <AmbientFieldCanvas>: 내용 뒤의 바탕 입자장(§4.6). 레이아웃에 한 번 둔다
  fx-provider.tsx          화면 전체 층 하나와 제어기. 레이아웃에 한 번 둔다
  reveal.tsx               useReveal(ref, options), <Reveal>
  orb.tsx                  <Orb mode level>: 작은 구(챗봇 빈 상태, 음성)
  use-fx.ts                useFxState, useFxEnabled, useFxMoves
  fx-settings.tsx          계정 메뉴의 "화면 효과" 스위치와 "인트로 다시 보기"
web/src/components/intro/  인트로 (S0)
web/src/components/topology/ + web/src/lib/topology/   3D 흐름도 (S11)
web/src/components/chat/   챗봇 화면 (S7), 다시 짠다
web/src/components/voice/ + web/src/lib/voice/         녹음, 재생, 상태
web/src/lib/rfq/chat-stream.ts                          스트림 읽기
src/autorfx/chat/events.py, progress.py                 진행 이벤트
src/autorfx/api/chat_stream.py, voice_routes.py         새 경로
src/autorfx/voice/                                      음성 제공자
```

점 대상은 요소에 `data-particle-target="text|line|box"`를 붙여 알린다. 점의 색은 `data-particle-tone="muted|accent"`로 바꾼다(기본 ink). 점의 색은 층을 만들 때 CSS 토큰(`--color-ink`, `-ink-muted`, `-accent`, `-danger`)에서 읽는다. 한 층의 점은 최대 9,000개, DPR은 2까지, 프레임 간격은 50ms로 자른다. 탭이 가려지거나 캔버스가 화면 밖이거나 움직일 점이 없으면 멈춘다. 점이 하나도 없는 층은 프레임도 그림도 없고, 포인터 이동도 루프를 깨우지 않는다(`layerNeedsFrame`, `layerWakesOnPointer`). 좌표는 층 캔버스의 CSS px이고, 화면 전체 고정 층에서는 뷰포트 좌표와 같다.

### 3.1 공개 접점

**켬·끔** (`lib/fx/prefs.ts`, 훅은 `components/fx/use-fx.ts`)

```ts
fxMoves(): boolean                       // 효과 켬 && 줄인 움직임 아님 && 브라우저. 움직이기 전에 늘 이것을 묻는다
getFxState(): { enabled, locked, reduced, moves }   // locked: 주소의 ?fx=0
setFxEnabled(on: boolean): void          // localStorage "autorfx.fx" (저장소가 막혀도 이 페이지에서는 동작)
subscribeFx(listener): () => void
requestIntroReplay(): void               // 계정 메뉴가 부른다
onIntroReplay(listener): () => void      // 인트로가 구독한다
useFxState() / useFxEnabled() / useFxMoves()
```

**화면 머리 등: 점에서 글자로** (`components/fx/reveal.tsx`, `fx-provider.tsx`)

```tsx
useReveal(ref, { duration?: number /*520*/, maxDots?: number /*1500*/, revealKey?: string | number }): void
<Reveal as="div|section|header|article" duration maxDots revealKey>…</Reveal>
useFx(): FxController | null             // 제공자 밖이면 null: 그때는 내용만 보인다
interface FxController {
  reveal(root: HTMLElement, o?: RevealOptions): { cancel(): void; done: Promise<void> }
  layer(): ParticleLayer | null          // 공유 전체 화면 층(처음 쓸 때 만든다). 효과가 꺼져 있으면 null
  remember(points: Float32Array): void   // "떠나는 화면"의 점: 다음 reveal이 여기서 출발한다
}
```

- 대상 요소는 `data-fx="wait"`(opacity 0, 자리 유지)로 두었다가 비행의 78% 지점에 `data-fx="in"`(460ms 겹침)으로 바꾸고 끝나면 속성을 지운다. 이 규칙은 `prefers-reduced-motion: no-preference` 안에서만 적용된다. `useReveal`은 `useLayoutEffect`에서 불러 첫 그림 전에 숨긴다. 언마운트하면 요소를 바로 보이고 점을 버린다. Strict Mode의 두 번 실행에도 맞다.
- 글꼴은 `document.fonts.ready`(최대 1.2초)를 기다린 뒤 표본을 뜬다. 같은 틱에 부른 reveal은 한 번의 `assemble`로 합친다. 더 새로운 reveal이 오면 앞의 것은 바로 보인다. 4초 안에 안 끝나도 요소는 보인다.
- **하드 로드에서는 깜박이지 않는다.** 문서를 처음 불러올 때 서버 HTML이 이미 그려 둔 글자는 `useReveal`/`<Reveal>`이 건드리지 않는다. 글자는 그대로 보이고 `data-fx` 속성도 붙지 않는다. 그 판단은 `lib/fx/first-load.ts`의 `shouldRevealOnMount({ isFirstCommit })`이다: 문서의 첫 커밋(제공자의 레이아웃 효과가 도는 그 커밋)에서 마운트된 것은 건너뛰고, 다음 틱부터 마운트되는 것(화면 이동, 서버 HTML에 없던 내용)만 점으로 나타낸다. 제어기(`FxController.reveal`)를 직접 부르는 쪽(인트로)은 이 판단을 받지 않고 늘 실행된다.

**점 층** (`lib/fx/particles.ts`)

```ts
new ParticleLayer(canvas, { max?: 9000, random?, colors? })
layer.assemble(groups: { points: Float32Array; delay?: number; tone?: Tone }[],
               { duration?: 900, jitter?: 180, from?: (k) => [x, y] }): Promise<void>  // 마지막 점이 앉으면 풀린다
layer.seed(points) / layer.scatter(n)    // 떠다니는 점을 더한다
layer.flow({ count, lanes: FlowLane[], speed?, spread?, restRadius? }): { update(lanes), stop() }
layer.release()                          // 점이 다시 떠다닌다
layer.fadeTo(target: 0..1, ms): Promise<void>
layer.exportPoints(): Float32Array       // 지금 위치(x, y 쌍)
layer.clear() / layer.destroy() / layer.dotCount
```

- `flow`의 관문 상태(`lib/fx/flow.ts`): `done` 점이 머문다, `error` 점이 머물며 `danger`색, `active` 점이 이 관문에서 다음(건너뜀이 아닌) 관문으로 되풀이해 흐른다, `skipped` 서지 않고 지나간다, `pending` 아무것도 닿지 않는다. 진행 중·완료·오류 관문이 하나도 없으면 점은 첫 관문에서 기다리고 움직임을 만들어 내지 않는다. **옮겨지는 점이 없으면 멈춘다**: 흘러갈 곳이 있는 `active` 관문이 하나도 없고(`isInTransit(lanes)`가 거짓) 모든 점이 자기 관문에 앉으면 층은 프레임 루프를 멈추고 마지막 그림을 그대로 둔다. `update(lanes)`가 `active` 관문을 되돌려 놓으면 다시 시작한다. 단계 타임라인은 차선 하나(관문 = 단계), 그래프는 간선마다 차선 하나(관문 2개)로 구동한다. `FlowLane = { gates: { x, y, state }[]; weight? }`.
- `Tone`: `TONE.ink | muted | accent | danger`(0~3).
- 점은 1, 1.5, 2px 네모이고 기기 픽셀 격자에 맞춘다. 발광, 흐림, 꼬리, 가산 혼합은 없다.

**구** (`lib/fx/sphere.ts`, 리액트는 `<Orb mode="idle|listening|thinking|speaking" level={0..1} size={96} />`)

```ts
new ParticleSphere(canvas, { count?: 900, radiusShare?: 0.36 })
sphere.setMode(mode) / setLevel(0..1) / setMorph(0..1) / pulse()
sphere.drawStill() / sphere.play()       // 효과 꺼짐: 한 장만 그린다
sphere.exportPoints(): Float32Array      // 뷰포트 좌표. 층의 assemble(from) 또는 seed에 넘긴다
sphere.destroy()
fibonacciLattice(n), project(...), rippleWeight(...), smoothLevel(...), SpikeDetector   // 순수
```

- **구는 실제 사건이 있을 때만 움직인다.** `idle`은 한 장만 그리고 프레임을 예약하지 않는다. `listening`·`speaking`은 `level`이 몰고, 동안에만 돈다. `thinking`은 타이머 물결이 없다: `<Orb pulseKey>` 값이 바뀔 때마다(스트림 사건 등) 물결이 한 번 퍼지고, 물결이 사라지면 루프가 멈춘다. `pulseKey`가 바뀌지 않으면 `thinking` 구도 멈춰 있다. 판단은 순수 함수 `sphereNeedsFrame`이다.

**CSS** (`globals.css`): 토큰 `--dur-fx-flight-min/-max`(520/900ms), `--dur-fx-jitter`(180ms), `--dur-fx-reveal`(460ms), `--dur-fx-cover`(480ms), `--z-fx-cover`, `--z-fx-layer`. 클래스 `fx-layer`(층), `fx-cover`(페이지색 덮개, `data-fx="out"`이면 걷힌다). 시간 값은 `lib/fx/motion.ts`의 상수와 같아야 하며 단위 테스트가 지킨다.

**아이콘** (`ui/icons.tsx`): `IconMic`, `IconMicOff`, `IconSpeaker`, `IconStop`, `IconSparkle`, `IconReplay`, `IconTopology`, `IconList`.

## 4. 화면

### 4.1 인트로 (S0)

브라우저 세션마다 한 번, 첫 화면 위에서 재생한다.

1. **구** (약 1.05초): 점 1,650개가 흩어져 있다가 구로 모인다. 포인터를 피한다.
2. **이름** (약 0.7초 + 머무름 0.36초): 구의 점이 그대로 "AutoRFx"와 한 줄 "RFQ를 조항으로 나누고, 모든 판단을 원문으로 되짚습니다."가 된다.
3. **넘김** (약 0.52초): 점이 아래에 있는 실제 화면(로그인이나 시작 화면)의 제목과 테두리로 날아가고, 78% 지점에서 실제 글자가 겹쳐 나타난다. 덮개가 걷힌다.

- 건너뛰기: 보이는 "건너뛰기" 버튼, Esc, 클릭. Tab은 삼키지 않는다.
- 다시 보기: 계정 메뉴의 "인트로 다시 보기", 주소의 `?intro=1`. `?intro=0`은 끈다.
- 글꼴이 준비된 뒤(`document.fonts.ready`)에 글자를 점으로 바꾼다. 실패하면 덮개를 걷고 화면을 그대로 보인다.

### 4.2 화면 머리 (모든 화면)

화면을 옮기면 떠나는 화면 머리의 점이 다음 화면의 제목과 범위 한 줄로 다시 모인다. 520ms 안에 끝나고 점은 1,500개까지다. 일하는 화면의 본문은 건드리지 않는다.

### 4.3 처리 진행 (S2)

여덟 단계 타임라인 옆으로 점이 흐른다. 진행 중인 단계에서 다음 단계로 내려가고, 끝난 단계에는 머문다. 오류 단계의 점은 `danger`로 멈추고, 건너뛴 단계는 머물지 않고 지나간다. `job` 응답의 상태만 따른다.

### 4.4 챗봇 (S7)

대화와 원문 뷰어의 2단은 그대로다.

- **빈 상태**: 작은 구와 안내 한 줄, 시작 질문 칩.
- **기다리는 동안**: 단계 줄(질문 이해 → 근거 찾기 → 답 쓰기 → 인용 확인)과 지난 시간. 작성된 문장 수만큼 점 줄이 늘어난다. 초안의 글자는 보이지 않는다.
- **답**: 검증된 문장이 점에서 글자로 차례로 나타난다(문장 사이 120ms, 전체 1.2초 안). 인용 칩, 충돌, 안내 문장의 뜻과 문구는 지금과 같다.
- **입력**: 글 입력, 보내기, 마이크(§6), "답변 읽어 주기" 스위치.

### 4.5 RFQ 흐름도 (S11, `/rfq/[id]/topology`)

받은 파일 묶음이 어떻게 조항이 되어 부서로 갔는지 한 장면으로 보인다. 이미 있는 응답(`document`, `clauses`, `consistency`)만 쓴다.

- 왼쪽에 파일(얇은 판), 가운데에 조항(파일별로 묶인 작은 상자), 오른쪽에 주관 부서. 조항에서 주관 부서로 선이 간다.
- 조항 상자는 인스턴싱으로 한 번에 그린다. 독소조항은 `danger`, 고른 것은 `accent`, 나머지는 먹색이다.
- 가려진 조항은 응답이 준 만큼만 그린다. 선이 없는 옅은 상자이고, 범례에 개수를 적는다(INV-8).
- 조항을 고르면 옆 패널에 번호, 제목, 주관·참조, 요건 강도가 나오고 조항 화면으로 가는 링크가 있다.
- 처음 열 때와 부서를 고를 때만 선을 따라 점이 흐른다. 그 밖에는 멈춰 있다(`frameloop="demand"`).
- WebGL이 없거나 장면이 실패하면 같은 내용을 표(부서별 조항 수, 파일별 조항 수)로 보인다. 표는 늘 "목록으로 보기"로 열 수 있다.
- 배치 계산은 순수 함수로 두고 단위 테스트한다.

### 4.6 바탕 입자장 (모든 화면)

페이지 바탕 전체에 점이 잔잔히 흐른다. 화면에 늘 있는 요소다(DESIGN.md §1).

- 화면 전체에 고정된 캔버스 한 장이 앱의 내용 뒤에 있다. 패널, 상단 바, 내비게이션은 불투명한 면이라 점은 그 사이와 둘레에서 보이고, 로그인과 시작 화면처럼 바탕이 넓은 곳에서는 화면을 채운다.
- 점은 흐름장을 따라 움직인다(초당 10~26px). 이웃한 점이 같은 쪽으로 흘러 물결처럼 보인다. 포인터 가까이에서는 비켜났다가 돌아오고, 화면을 벗어나면 반대쪽에서 다시 들어온다.
- 점의 수는 화면 넓이를 따른다(약 700px²에 하나, 최대 3,200개). 크기 1~2px, 불투명도 0.25~0.7. 대부분 먹색이고 일부가 보조 글자색이다. 강조색은 선택과 링크의 색이라 바탕에는 쓰지 않는다.
- 인트로의 점, 화면 머리의 점, 답의 점은 이 입자장 위에서 따로 움직이고, 입자장은 그동안에도 흐른다.
- `prefers-reduced-motion`이면 멈춘 점을 한 번 그린다. "화면 효과"를 끄면 그리지 않는다. 탭이 가려지면 멈춘다.
- 흐름장과 점의 수는 순수 함수로 두고 단위 테스트한다.

## 5. 챗봇 스트리밍

`POST /rfqs/{rfq_id}/chat/stream`. 요청은 `POST /rfqs/{rfq_id}/chat`과 같고 응답은 `text/event-stream`이다. 한 이벤트는 `data: <JSON>` 한 줄이다.

| `type` | 내용 | 언제 |
|---|---|---|
| `stage` | `stage`: `plan`, `search`, `write`, `verify`, `rewrite`. `write`에는 `evidence`(근거 문장 수) | 그 단계를 시작할 때 |
| `progress` | `drafted`: 지금까지 작성된 문장 수 | 모델이 답을 쓰는 동안 |
| `done` | `response`: `POST /chat`과 같은 응답 전체 | 끝. 마지막 이벤트 |
| `error` | `error`: `{code, message}` | 스트림을 연 뒤의 실패. 마지막 이벤트 |

- **검증 뒤에만 보인다.** 초안의 글자는 스트림에 싣지 않는다. 사용자는 단계와 문장 수를 보고, 답은 `done`에서 검증된 문장으로 한 번에 받는다(CITE-01, CITE-02).
- 질문 형식 오류, 인증, 권한, 분석 중은 스트림을 열기 전에 지금과 같은 상태 코드와 JSON 오류로 답한다.
- 같은 입력에서 `done.response`는 `POST /chat`의 응답과 같다. 답을 만드는 경로는 하나이고, 스트림은 그 경로를 지켜보기만 한다.
- 문장 수는 LangChain 콜백(`on_llm_new_token`, 모델은 `streaming=True`)으로 받은 조각에서 센다. 스트리밍 호출이 제공자 장애가 아닌 이유로 실패하면 스트리밍 없는 같은 호출로 한 번 다시 한다. 콜백이 실패해도 답에는 영향이 없다.
- LLM이 없으면 `stage`만 흐르고 `done`이 온다.
- 화면은 스트림이 `done` 전에 끊기거나 경로가 없으면 `POST /chat`으로 한 번 다시 묻는다.

### 5.1 이벤트 순서

첫 이벤트는 늘 `stage: plan`이고 마지막은 `done`이나 `error`다. 단계는 되풀이될 수 있다.

| 경로 | 순서 |
|---|---|
| 금액 질문 거절, 안내(`guide`) | `plan` → `done` |
| 저장된 독소조항·정합성 | `plan` → `verify` → `done` |
| LLM 없음 (원문 발췌) | `plan` → `search` → `verify` → `done`. 근거가 없으면 `verify` 없이 `done` |
| LLM 답 | `plan` → `search` → `write`(`evidence`) → `progress`… → `verify` → `done` |
| LLM 답, 다시 쓰기 | … `verify` → `rewrite` → `progress`… → `verify` → `done` |
| LLM 장애로 원문 발췌 | … `write` → `search` → `verify` → `done` |

`progress.drafted`는 한 번의 작성 안에서 줄지 않고, `rewrite` 뒤에 0부터 다시 센다. `done` 앞의 어떤 이벤트에도 답의 글자가 없다.

### 5.2 코드 접점

- `autorfx.chat.service.answer_question(..., observer=None)`: `observer`는 계약 모양의 dict 하나를 받는 함수다(`{"type": "stage", "stage": "plan"}`). `stage`와 `progress`만 보낸다. `observer`가 예외를 내도 답은 같다. `None`이면 지금과 똑같이 동작한다.
- `ChatModel.answer(..., on_progress=None)`: 작성된 문장 수를 받는 함수. `ScriptedChatModel`은 준비된 초안의 문장 수만큼 1부터 차례로 부른다.
- `autorfx.chat.progress.count_statements(partial_json: str) -> int`: 덜 끝난 JSON 문자열에서 끝까지 쓰인 문장(`statements`의 닫힌 객체) 수. 문자열 안의 따옴표와 중괄호에 속지 않는다.
- `OpenAIChatModel(model, *, plan_call, answer_call, answer_stream_call=None)`: 스트리밍 호출은 `answer_stream_call(system, user, schema, on_token)`이고 `on_token`은 모델이 낸 글자 조각을 받는다. `on_progress`가 있고 스트리밍 호출이 주입돼 있으면 그것을 쓰고, 조각을 모아 `count_statements`가 늘 때마다 `on_progress`를 부른다.
- `autorfx.chat.model.is_outage(exc) -> bool`: 시간 초과와 연결 실패면 참(`TimeoutError`, `ConnectionError`, httpx와 OpenAI SDK의 같은 뜻의 예외).
- `autorfx.chat.model.worth_asking_without_streaming(exc) -> bool`: 스트리밍 호출이 **우리 쪽 이유**(파싱, 콜백, 라이브러리 불일치)로 실패했을 때만 참이고, 그때 일반 호출로 한 번 다시 한다. 제공자에 닿지 못했거나, 시간이 넘었거나, 제공자가 오류(429, 5xx 등)나 거절로 답했으면 다시 하지 않고 `TransientProviderError`를 낸다. 같은 답을 두 번 기다리지 않기 위해서다.
- 스트리밍한 답 하나의 전체 시간은 일반 호출의 시간 한도(45초)와 같다. 넘으면 시간 초과로 다룬다. 콜백 안의 다른 오류는 삼켜져 답을 바꾸지 못한다.
- 스트리밍 경로에서 초안의 글자가 경고나 로그로 새지 않게 그 경고 하나를 막는다(문서의 값이 로그에 남으면 안 된다).
- 스트림의 답은 앱이 가진 전용 스레드 풀(`AUTORFX_CHAT_STREAM_WORKERS`, 기본 8)에서 만든다. 끊긴 스트림이 다른 요청의 스레드를 붙잡지 않는다.
- 요청 본문의 한도는 바이트로 센다. 글자 한도(질문 500자, 기록 6턴 × (500 + 4,000자))를 UTF-8 최대 4바이트로 환산해 128KiB다.
- `autorfx.api.chat_stream`: 경로와 SSE 직렬화. 답은 작업 스레드에서 만들고 이벤트는 큐로 넘긴다. 클라이언트가 끊어도 서버에 예외를 남기지 않는다.

## 6. 음성

기본은 꺼짐이다. OpenAI 키가 있고 `VOICE_ENABLED=true`일 때만 켜진다(OQ-FX-2가 정해질 때까지 켜겠다고 한 곳에서만 녹음이 밖으로 나간다). 꺼져 있으면 `/meta`의 `voice`가 꺼짐으로 오고 마이크와 읽기 스위치가 보이지 않는다. 글로 묻는 기능은 그대로다.

- **질문 (STT)**: 브라우저가 녹음(`MediaRecorder`, 최대 60초)해 `POST /voice/transcriptions`(multipart, 필드 `audio`)로 보낸다. 서버가 글자로 바꿔 돌려주고, 화면은 그 글자를 질문으로 보낸다. 500자를 넘으면 보내지 않고 입력 칸에 넣어 고치게 한다.
- **답 읽기 (TTS)**: `POST /voice/speech`(`{text}`)가 `audio/mpeg`를 돌려준다. 읽는 것은 화면에 보인 검증된 문장뿐이다(`citation_status`가 `verified`인 답). 답이 없을 때는 서버가 준 안내 문장을 읽는다(문서의 글자가 아니다). 인용 위치와 파일 이름은 읽지 않는다.
- 브라우저의 음성 인식(Web Speech API)은 쓰지 않는다. 소리를 팀이 정하지 않은 제3자에게 보내기 때문이다. 음성은 챗봇의 LLM과 같은 제공자로만 간다.
- 용어집(`data/rules/glossary.yaml`)의 낱말을 인식 힌트로 준다. 문서의 문장은 힌트에 넣지 않는다.
- 제공자는 인터페이스 뒤에 둔다(`SpeechProvider`). 테스트는 대역을 쓰고 네트워크가 필요 없다.
- 상태: 대기 → 권한 요청 → 녹음(구가 입력 크기를 따른다) → 변환 → (질문 전송) → 읽기(멈춤 버튼). 마이크를 누르면 읽기가 멈춘다.
- 한도: 녹음은 화면이 60초에서 끊고, 서버는 2MiB를 넘는 소리(413)와 2,000자를 넘는 인식 결과(422)를 받지 않는다. 서버는 길이를 재지 않으므로 크기 한도가 길이 한도를 대신한다. 읽기는 1,200자까지다(422).

### 6.1 코드 접점

- `autorfx.voice.SpeechProvider`(Protocol): `id: str`, `transcribe(audio: bytes, *, mime: str, hint: str) -> str`, `speak(text: str) -> bytes`(MP3). 지금 답할 수 없으면 `TransientProviderError`.
- `autorfx.voice.ScriptedSpeech`: 테스트 대역. 받은 호출을 기록하고 준비된 글자와 소리를 돌려주거나 예외를 낸다.
- `autorfx.voice.make_speech(settings) -> SpeechProvider | None`: 키가 없거나 `VOICE_ENABLED`가 `true`가 아니면 `None`(기본값과 빈 값은 꺼짐).
- `create_app(..., speech=None)`으로 주입하고 `AppState.speech`에 둔다.
- 경로 `POST /voice/transcriptions`, `POST /voice/speech`는 `autorfx.api.voice_routes`에 둔다. 로그인이 필요하고, 제공자가 없으면 503 `voice_unavailable`, 제공자 실패는 503 `voice_failed`와 제공자 정보를 담지 않은 문장이다.
- 받는 소리 형식: `audio/webm`, `audio/mp4`, `audio/mpeg`, `audio/wav`, `audio/ogg`. 그 밖은 415.
- 음성 변환 응답의 출처 표시는 `chat: "real"`이다.

## 7. 계약 추가

- `ChatStreamEvent`(§5의 네 가지), 스키마 이름 `chat-stream-event`.
- `MetaResponse.data.voice`: `{transcribe, speak, max_seconds, max_speech_chars}`.
- `VoiceTranscriptionResponse`: `{text}`의 봉투, 스키마 이름 `voice-transcription`.
- `VoiceSpeechRequest`: `{text}`(1~1,200자), 스키마 이름 `voice-speech-request`.
- `engines`는 바꾸지 않는다(저장된 분석이 오래된 것으로 바뀌기 때문이다).

## 8. 성능, 접근성, 검사

- 입자가 꺼져도 모든 화면의 내용과 조작이 같다. 키보드로 인트로를 건너뛰고, 흐름도의 내용을 표로 읽고, 음성 없이 묻는다.
- 대화 기록은 `aria-live="polite"`다. 단계 이름만 읽고 초와 문장 수는 읽지 않는다.
- 단위 테스트: 비행 곡선과 표본 줄이기, 구 격자, 스트림 읽기(조각난 줄, 끊김), 읽을 문장 고르기, 흐름도 배치. 백엔드는 이벤트 순서, 스트림과 일반 응답의 일치, 음성 경로의 한도와 권한.
- e2e는 줄인 움직임으로 돌려 기존 흐름이 인트로에 막히지 않게 한다. 입자를 보는 검사만 움직임을 켠다.
- 화면 확인은 320, 768, 1024, 1440 폭에서 한다.

## 9. 미결 사항

- **OQ-FX-1 (닫음, 2026-11-25)** 입자 배경은 권장이다. Tech Lead가 정했고 DESIGN.md §1, §4.3, §10을 고쳤다.
- **OQ-FX-2** 음성을 외부 제공자로 보내는 것을 팀과 회사 멘토가 받아들이는가. 정해질 때까지 기본은 꺼짐이고 `VOICE_ENABLED=true`인 곳에서만 켜진다.
- **OQ-FX-3** 초안을 "검증 전"이라고 표시해 글자로 보여 줄 것인가. 지금은 보이지 않는다.
- **OQ-FX-4** 음성 경로의 사용량 제한. 지금은 길이와 크기 한도, 로그인만 있다.
