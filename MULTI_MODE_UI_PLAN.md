# Multi Mode UI/기능 기획 (Draft)

## 목표
- 기존 Single 모드는 유지하면서, **Multi 모드**에서 최대 **8개 슬롯(중복 ID 허용)** 을 구성해 **주기적으로 CAN 메시지를 송신**한다.
- Multi 모드에서 **DBC 전체 메시지 탐색 → 메시지 선택 → 선택된 메시지만 편집** 흐름을 제공한다(뒤로가기 포함).
- 각 슬롯마다 **미니 그래프 1개**를 두고, 해당 슬롯의 **ID + 메시지 + 선택한 signal 1개**를 그래프로 표시한다.
- 성능 최적화:
  - 그래프는 **최근 500포인트(≈5초 @ 10ms)** 만 유지한다.
  - **선택된 탭에서만 그래프 redraw**를 수행하고, 비활성 탭에서는 redraw를 중단한다(데이터 버퍼는 계속 쌓되 maxlen 유지).

## 주요 요구사항(확정)
- Multi에서:
  - 메시지 리스트는 **DBC 전체**를 보여준다.
  - 메시지를 선택하면 해당 메시지 편집 화면으로 전환하고, `Back`으로 전체 리스트로 복귀한다.
  - **중복 ID 허용**(속도/게인 등 서로 다른 메시지를 같은 ID에 동시에 튜닝 가능).
  - 슬롯 수는 **최대 8개**(중복 포함).
  - 모든 메시지의 송신 주기는 **동기화(공통 Period)**.
  - `Start` 시 **메시지 미선택 슬롯은 자동으로 송신 대상에서 제외(무시)**.
- 추가:
  - 메시지 브라우저에 **검색창(Search)** 제공.

## UI 구성(제안)
### 상단
- `Single | Multi` 토글(권장: `QTabWidget`)

### Multi 탭(레이아웃)
- 상단 공통 바:
  - `Period(ms)` SpinBox (전체 슬롯 공통)
  - `Start/Stop` 토글
  - (옵션) `Send Once`
  - (옵션) `Active: n/8`

- 본문 2열:
  1) 좌측: **슬롯 리스트(최대 8개)** + `+ Add` 버튼
  2) 우측: `QStackedWidget` 기반 **Message Browser / Message Editor**

## 슬롯(1개 행/카드) 구성
- 상단: 작은 `PlotWidget` (해당 슬롯의 그래프)
- 하단 컨트롤:
  - `Enable` 체크
  - `ID` SpinBox(1~31)
  - `Message` 표시(미선택이면 `Not selected`)
  - `Graph Signal` ComboBox (메시지 선택 후 해당 메시지 signal 목록으로 채움)
  - `Edit` 버튼 (우측 Editor로)
  - `Delete(−)` 버튼
- `+ Add`로 슬롯 추가, 8개 도달 시 비활성화

## Message Browser (DBC 전체 + 검색)
- 상단: `Search` 입력창(타이핑 시 즉시 필터)
- 리스트: DBC 메시지 전체 표시(권장 표시: `name`, `frame_id`, `dlc`)
- 항목 클릭 동작:
  - “현재 선택된 슬롯”에 message 지정
  - `Graph Signal` 목록 갱신(기본값: 첫 signal)
  - Editor 페이지로 이동

## Message Editor (선택된 msg만 편집 + Back)
- 상단: `← Back` 버튼
- 본문:
  - 선택된 message의 signal들을 자동 폼 생성
    - 숫자: `SpinBox/DoubleSpinBox`
    - enum/choices: `ComboBox`
    - boolean: `CheckBox`
- 저장 단위: “슬롯 단위”
  - 같은 ID/같은 message라도 다른 슬롯이면 값은 독립적

## 송신 동작(동기화)
- Multi는 `QTimer` **1개**로 동작
- tick마다:
  - 슬롯 리스트를 위→아래 순회
  - **유효 슬롯 조건**: `Enable == True` && `Message selected == True`
  - 각 슬롯별로 `ID + message + signal 값`을 인코딩하여 전송
- `Start` 시 메시지 미선택 슬롯은 자동 제외(경고 팝업 없이 UI 상태로만 표시)

## 수신 및 그래프 표시(미니 그래프)
- 수신 메시지는 기존처럼 디코딩하되, 슬롯의 매칭 기준을 둔다:
  - 슬롯의 `ID`와 수신 `upper_5_bits_id`가 일치
  - 슬롯의 `message.frame_id`와 수신 `cmd/frame`이 일치(프로젝트의 frame 구성 규칙에 맞춤)
  - 일치 시, 슬롯이 선택한 `Graph Signal` 값만 추출
- 데이터 버퍼:
  - 각 슬롯은 `(t, y)`를 `deque(maxlen=500)`으로 유지

## 성능/리소스 정책(탭 기반 redraw 게이팅)
- `active_tab == Multi`일 때만:
  - Multi의 8개 미니 그래프에 대해 `setData(...)` 등 redraw 수행
- `active_tab != Multi`이면:
  - 데이터 버퍼는 계속 갱신하되 **그래프 redraw는 중단**
- 동일하게 Single의 메인 그래프도:
  - Single 탭일 때만 redraw 수행(비활성 시 중단)

## 예외/정책
- 중복 ID 허용(동일 ID 슬롯 여러 개 OK)
- 슬롯이 Enable이지만 message 미선택이면 송신 대상 제외(무시)
- 최대 슬롯 8개 제한(초과 추가 불가)

## 구현 체크리스트(후속 작업)
- Multi 탭 UI 추가(슬롯 리스트 + stacked browser/editor)
- DBC 메시지 검색 필터 구현
- 슬롯별 signal 폼 자동 생성/값 저장 구조 정의
- Multi 송신 타이머 추가(공통 period)
- 수신 매칭 로직 추가(슬롯 그래프용)
- 그래프 버퍼 `maxlen=500` 적용 및 탭 기반 redraw 게이팅 적용

