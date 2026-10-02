# 밥심(Bapsim) — HA·AI·Matter 개발 컨텍스트 인계 요약

작성 기준: 2026-10-02  
목적: 이 파일을 새 ChatGPT Work/Codex 세션에 넣으면, 지금까지 정리한 **Home Assistant 담당 범위, 밥심 판정 코어, Colab 기반 가중치 탐색, 선택적 Matter 컨텍스트 확장, 하드웨어 담당과의 경계**를 다시 설명하지 않고 바로 구현 계획을 이어갈 수 있게 한다.

---

## 0. 가장 먼저 이해할 핵심

밥심은 **밥솥 사용 행동의 변화를 관측해 “안부 확인이 필요한지” 판단하는 시스템**이다.

중요한 표현 원칙:

- 밥솥 사용 데이터만으로 실제 식사 여부, 건강 상태, 질병을 확정하지 않는다.
- “위험 감지”라는 표현을 구현 내부에서 쓸 수는 있지만, 발표/문서에서는 가급적  
  **생활 루틴 변화**, **확인 필요도**, **평소 패턴 이탈**로 설명한다.
- 뚜껑 개폐는 실제 섭취의 확정 증거가 아니라 **식사 관련 행동의 대리지표**다.

현재 프로젝트에서 사용자는 **Home Assistant / 서비스 / 분석 쪽 담당**이다.

하드웨어·Matter 펌웨어 담당과의 경계는 다음과 같다.

```text
[하드웨어/Matter 담당]

실제 밥솥 외부 센서
→ nRF54LM20-DK
→ 센서 입력 처리
→ Matter Contact Sensor 등으로 노출
→ Thread / Commissioning
→ Matter 상태 전달

================ 인계 경계 ================

[사용자 담당]

Home Assistant에서 Matter 상태 수신
→ Cooker Adapter
→ Event Normalizer
→ Session Builder
→ Feature Extractor
→ Personal Baseline
→ Detection / Calibration
→ Optional Matter Context Fusion
→ 알림 / 보호자 응답 / Dashboard
```

즉, 사용자의 핵심 개발은 “Home Assistant 설정” 그 자체보다:

> **밥솥 데이터 → 공통 이벤트 → 행동 특징 → 개인 기준 비교 → 확인 필요 판단**

을 코드화하는 것이다.

---

# 1. 전체 시스템 구조

현재 가장 적절한 구조는 다음과 같다.

```text
                         INPUT LAYER

         ┌───────────── 밥솥 API ─────────────┐
         │                                     │
         ├──────── Matter 밥솥/외부센서 ──────┤
         │                                     │
         └──────────── Simulator ──────────────┘
                           │
                           ▼
                 Cooker Event Normalizer
                           │
                           ▼
                  Cooking Session Builder
                           │
                           ▼
                  Cooker Feature Extractor
                           │
                           ▼
                   Personal Baseline
                           │
                           ▼
                 Cooker Detection Core
                           │
                           │
            ┌──────────────┴──────────────┐
            │                             │
            │                     Optional Matter Context
            │                             │
            │            모션 / 현관 / 조명 / 플러그 / 기타
            │                             │
            │                      Context Normalizer
            │                             │
            │                      Context Features
            │                             │
            └──────────────┬──────────────┘
                           ▼
                    Evidence Fusion
                           │
                           ▼
                Detection State Manager
                           │
                           ▼
        Home Assistant / 알림 / Dashboard / 보호자 응답
```

핵심 철학:

- **밥솥이 Primary signal**
- 다른 Matter 기기는 **Optional context**
- 다른 Matter 기기가 0개여도 밥심은 정상 동작
- 다른 기기가 있으면 판단의 보조 근거로 사용
- Matter 기기 미보유/미지원은 `0`이 아니라 `unavailable/not_present`로 처리

---

# 2. 개발 영역별 분리

집에서 Codex를 돌릴 때 한 번에 전부 만들지 말고 아래처럼 작업 단위를 분리한다.

## 01. Home Assistant Foundation

역할:

- HA 실행 환경 구성
- 프로젝트 설정 분리
- automations / scripts / packages / dashboards 구조
- 로그 및 History 확인
- Companion 앱 연동 준비

이 단계에서는 밥심 알고리즘을 넣지 않는다.

권장 작업명:

`01-home-assistant-foundation`

---

## 02. Input Adapters

입력 출처별 코드를 분리한다.

```text
inputs/
├─ simulator/
├─ smartthings/
├─ xiaomi_miot/
├─ matter/
└─ base/
```

각 Adapter는 “판정”을 하지 않는다.

역할은 오직:

- 외부 데이터를 읽는다.
- 연결 상태를 확인한다.
- 원시 상태를 공통 계층에 넘긴다.

예:

```python
fetch_state()
subscribe_events()
check_availability()
```

가능한 입력원:

1. 제조사 API
2. nRF/Matter 센서
3. 가상 Simulator

현재 프로젝트 일정상 실제 제조사 API가 완전히 검증되지 않아도 전체 서비스가 막히지 않도록 Simulator/Matter 입력을 독립적으로 지원해야 한다.

---

# 3. Cooker Event Normalizer

제조사/API/Matter마다 다른 데이터를 **밥심 내부 공통 이벤트**로 바꾼다.

권장 공통 필드:

```text
device_id
event_type
value
observed_at
received_at
source
availability
quality
```

대표 이벤트:

```text
lid_opened
lid_closed
cooking_started
cooking_finished
keep_warm_started
keep_warm_ended
device_online
device_offline
```

중요 규칙:

- 값이 없으면 `unknown/unsupported`
- API 단절은 `offline/unavailable`
- 없는 값을 `0`, `닫힘`, `정상`으로 채우지 않는다.
- 반복 상태 보고를 새 이벤트로 세지 않는다.
- `false → true` 같은 상태 전이가 있을 때만 새 개방 이벤트를 생성한다.
- 센서 관측 시각과 서버 수신 시각을 구분한다.
- 짧은 접점 흔들림은 debounce한다.

권장 작업명:

`03-cooker-event-normalizer`

---

# 4. Cooking Session Builder

원시 이벤트를 하나의 취사 세션으로 묶는다.

예:

```text
07:00 cooking_started
07:42 cooking_finished
07:42 keep_warm_started
08:05 lid_opened
08:07 lid_closed
```

↓

```json
{
  "cooking_started_at": "07:00",
  "cooking_finished_at": "07:42",
  "first_open_at": "08:05",
  "first_open_delay_min": 23,
  "lid_open_count": 1
}
```

처리할 것:

- 취사 시작/완료 연결
- 취사 완료 후 첫 개방 탐색
- 반복 개방 횟수 계산
- 다음 취사와 세션 분리
- 중간 데이터 단절 표시
- 불완전 세션 표시

권장 작업명:

`04-cooking-session-builder`

---

# 5. Cooker Feature Extractor

판정/AI에 사용할 숫자를 만든다.

## 핵심 Feature 후보

```text
cook_count_24h
lid_open_count_24h
hours_since_last_open
median_first_open_delay
cook_interval_hours
```

개인 기준 대비 Feature:

```text
open_count_ratio
first_open_delay_ratio
cook_frequency_ratio
hours_since_last_open_ratio
```

핵심 파생 Feature:

```text
open_count_change
first_open_delay_change
cook_frequency_change
cook_open_mismatch
```

가장 중요한 밥심 시나리오:

> **취사 빈도는 유지되는데 개방 횟수는 줄거나, 취사 후 첫 개방 시간이 길어지는 현상**

즉 단순 “밥솥을 안 썼다”가 아니라 **취사와 개방 행동의 괴리**를 본다.

권장 작업명:

`05-behavior-feature-extractor`

---

# 6. Personal Baseline Engine

밥심은 모든 사람에게 같은 기준을 적용하지 않는다.

예:

```text
사용자 A
평소 첫 개방: 10~30분
현재: 90분
→ 큰 변화

사용자 B
평소 첫 개방: 60~100분
현재: 90분
→ 정상 범위
```

따라서 절대값보다 개인 baseline 대비 변화량을 Feature로 쓴다.

권장 초기 통계:

- median
- quantile
- rolling trend
- 최근 N일 범위

핵심 원칙:

```text
개인화되는 것:
- baseline

여러 사용자에게 공통 적용하는 것:
- feature 정의
- weight
- threshold
```

Baseline contamination 방지:

```text
NORMAL 데이터
→ baseline 업데이트 가능

CHECK_REQUIRED / 이상 기간
→ baseline 업데이트 보류 또는 제한
```

권장 작업명:

`06-personal-baseline-engine`

---

# 7. Cooker Detection Core

입력:

```text
현재 Cooker Feature
+ Personal Baseline
+ Data Quality
```

출력 예:

```json
{
  "routine_status": "check_required",
  "severity": "warning",
  "reason_codes": [
    "FIRST_OPEN_DELAY_HIGH",
    "LID_ACTIVITY_DECREASED"
  ],
  "score": 0.82
}
```

## 세부 Detector

### 7-A. Normal

개인 평소 범위 내.

### 7-B. Gradual Decline

며칠에 걸쳐 개방/취사 행동이 감소.

### 7-C. Cook–Open Mismatch

가장 중요한 밥심 Detector.

```text
취사는 평소와 비슷
AND
개방 활동 감소
AND/OR
첫 개방 지연 증가
```

### 7-D. Acute Inactivity

수집 자체는 정상인데 평소 있어야 할 활동 이벤트가 없음.

### 7-E. Away Handling

외출/부재 상태가 명시된 경우 무사용만으로 경고하지 않음.

### 7-F. Data Quality

API 인증 만료, 통신 끊김, 센서 단절 등.

중요:

```text
데이터 없음
≠
활동 없음
```

통신 장애인 경우 생활 이상으로 판정하지 않는다.

---

# 8. Rule Gate

AI/가중치 판정 앞뒤에 명백한 예외를 처리하는 계층을 둔다.

예:

```text
availability == offline
→ 생활 변화 판단 금지

away == true
→ 알림 보류

baseline 데이터 부족
→ learning / insufficient_data
```

즉 모든 것을 AI에게 맡기지 않는다.

---

# 9. Google Colab — 가상 데이터 기반 Calibration

현재 사용자의 AI 목표는 “운영 중 계속 학습하는 복잡한 AI”가 아니라:

> **여러 가상 사용자/시나리오에서 공통으로 잘 작동하는 범용 weight와 threshold를 찾고, 최종 런타임 코드에 고정하는 것**

에 가깝다.

정확한 표현:

**offline data-driven parameter calibration → fixed scoring rule**

예:

```text
risk_score =
    w1 × 개방횟수 변화
  + w2 × 첫 개방 지연
  + w3 × 취사-개방 괴리
  + w4 × Matter context
```

Colab에서 `w1~w4`, threshold를 탐색하고 최종 config에 저장한다.

---

# 10. Colab 실험 파이프라인

권장 노트북/작업 분리:

```text
ai/
├─ 01_synthetic_population.ipynb
├─ 02_scenario_generator.ipynb
├─ 03_feature_pipeline.ipynb
├─ 04_rule_baseline.ipynb
├─ 05_weight_calibration.ipynb
├─ 06_logistic_regression.ipynb
├─ 07_matter_context_ablation.ipynb
├─ 08_evaluation.ipynb
└─ export/
```

## AI-01. Synthetic Population

사람마다 다른 정상 패턴 생성.

예:

```text
User A:
아침 위주 취사
첫 개방 빠름

User B:
점심/저녁 취사
첫 개방 느림

User C:
불규칙 생활
개방 편차 큼
```

## AI-02. Scenario Generator

최소 시나리오:

1. Normal
2. Gradual Decline
3. Cook–Open Mismatch
4. Acute Inactivity
5. Away
6. Communication Failure
7. 센서 노이즈/일시 누락
8. 사용자별 정상 변동성

중요:

가상 데이터의 “정답”을 원하는 weight 공식으로 만들면 안 된다.

잘못된 예:

```text
0.4*delay + 0.3*open + 0.3*mismatch > 0.7
→ 위험
```

이 방식으로 라벨을 만든 뒤 AI가 같은 weight를 복원하면 의미가 없다.

대신 **상황 자체를 생성**한다.

예:

```text
Cook–Open Mismatch:
- 취사 빈도 유지
- 개방 빈도 점진적 감소
- 첫 개방 지연 증가
```

그 후 모델/최적화가 weight를 찾게 한다.

---

# 11. 범용 가중치 탐색

비교할 후보:

### A. 수동 Rule Baseline

사람이 정한 초기 임계값.

### B. Optimized Weighted Rule

Grid Search / Random Search / Optimization 등으로:

```text
w1, w2, w3, ...
threshold
```

탐색.

### C. Logistic Regression

입력:

```text
x1 = baseline 대비 개방횟수 변화
x2 = baseline 대비 첫 개방 지연
x3 = 취사-개방 괴리
x4 = Matter 생활활동 변화
```

학습된 coefficient 자체를 “데이터 기반 공통 weight 후보”로 활용 가능.

### D. Small Neural Network / Anomaly Detection

선택 확장.

최종 서비스가 설명 가능한 고정 가중치를 목표로 한다면 필수는 아니다.

---

# 12. 범용성 검증

한 번의 synthetic dataset에서 잘 나온 weight를 바로 고정하지 않는다.

반드시:

- 가상 사용자 구성 변경
- random seed 변경
- 정상 변동성 변경
- 일부 센서 누락
- 다른 scenario mixture

에서도 비슷하게 동작하는지 본다.

평가 후보:

```text
False Positive Rate
False Negative Rate
Precision
Recall
F1
```

핵심 목표는 “가상 데이터에서 최고 점수”보다:

> **여러 population과 조건에서 성능이 안정적인 weight/threshold를 찾는 것**

이다.

---

# 13. Optional Matter Context — 핵심 확장

Matter 대회에서 밥솥 하나만 연결하면:

> “이 기능은 Matter 없이 Wi-Fi/API만으로도 가능한 것 아닌가?”

라는 질문이 가능하다.

따라서 Matter를 단순 통신수단이 아니라:

> **집 안의 다른 제조사 Matter 기기들을 선택적 생활 맥락 센서로 활용하는 상호운용성 계층**

으로 사용한다.

구조:

```text
밥솥 = Primary signal

Optional Matter Context:
- Motion / Occupancy
- Door Contact
- Light
- Smart Plug
- 기타 생활 활동을 나타낼 수 있는 Matter 기기
```

원칙:

- 밥솥 하나만 있어도 완전하게 동작
- 다른 기기가 있으면 판단 보조
- 다른 기기 없으면 빈 상태로 유지
- optional device가 없다는 사실을 “활동 0”으로 해석하지 않음

---

# 14. Matter Context Device Registry

집에 어떤 보조 기기가 있는지 관리.

예:

```json
{
  "devices": [
    {
      "device_id": "motion_01",
      "role": "activity_context",
      "capabilities": ["occupancy"]
    },
    {
      "device_id": "door_01",
      "role": "activity_context",
      "capabilities": ["contact"]
    }
  ]
}
```

구분:

```text
기기 없음
→ not_present

기기 있음 + 정상 연결
→ available

기기 있었지만 현재 단절
→ unavailable
```

권장 작업명:

`09-matter-context-device-registry`

---

# 15. Matter Context Adapter / Normalizer

다른 Matter 기기의 상태를 공통 “생활 활동 관측” 이벤트로 변환.

예:

```json
{
  "context_type": "household_activity",
  "event_type": "motion_detected",
  "observed_at": "...",
  "device_id": "motion_01"
}
```

현관:

```json
{
  "context_type": "household_activity",
  "event_type": "door_opened",
  "observed_at": "...",
  "device_id": "door_01"
}
```

조명:

```json
{
  "context_type": "device_usage",
  "event_type": "light_turned_on",
  "observed_at": "..."
}
```

여기서는 의미를 과장하지 않는다.

예:

```text
motion detected = 실제 생활 활동 관측
```

이지:

```text
motion detected = 건강함
```

이 아니다.

---

# 16. Context Feature Extractor

보조 기기의 RAW 이벤트를 Feature로 변환.

예:

```text
motion_events_6h
door_events_6h
light_active_minutes_6h
hours_since_last_household_activity
active_device_count_6h
```

가능하면 개인/가구의 평소 맥락과 비교:

```text
motion_activity_ratio
door_activity_ratio
overall_activity_change
```

---

# 17. Missing-device / Availability Mask

AI/가중치 모델에서 매우 중요.

잘못된 처리:

```text
모션센서가 없는 집:
motion_count = 0
```

이러면 모델이 “집안 활동이 0회”라고 오해한다.

권장:

```text
motion_activity = value
motion_available = 1
```

기기가 없으면:

```text
motion_activity = missing/default
motion_available = 0
```

현관/조명도 동일.

Optional context의 핵심은 **graceful degradation**이다.

---

# 18. Evidence Fusion Engine

밥솥 판정을 다른 Matter 기기보다 우선한다.

예:

### Case A — Cooker only

```text
밥솥 이상
+ 다른 Matter context 없음

→ CHECK_REQUIRED
→ context = cooker_only
```

### Case B — Corroborating

```text
밥솥 이상
+ 다른 생활 활동도 감소

→ CHECK_REQUIRED
→ context = corroborating
```

### Case C — Conflicting

```text
밥솥 이상
+ 집안 다른 활동은 평소 수준

→ 밥솥 변화 자체는 유지
→ context = conflicting
→ 확인 필요도를 낮추거나 근거 문구 변경
```

### Case D — Context unavailable

```text
밥솥 이상
+ 보조 기기 통신 단절

→ 밥솥 판단 유지
→ context = unavailable
```

중요:

다른 Matter 기기가 밥솥 판단을 “대체”하지 않는다.

---

# 19. Matter Context 가중치 처리

가능한 최종 scoring 개념:

```text
Cooker:
w1 * open_change
w2 * delay_change
w3 * cook_open_mismatch

Context:
w4 * motion_change
w5 * door_change
w6 * light_change
```

하지만 사용 가능한 context만 포함해야 한다.

예:

```text
밥솥만 있음
→ Cooker feature만으로 score

밥솥 + Motion
→ Cooker + Motion

밥솥 + Motion + Door
→ Cooker + Motion + Door
```

가중치 재정규화 또는 별도 availability-aware scoring을 사용한다.

---

# 20. Matter Context Ablation

“다른 Matter 기기가 정확도를 올린다”는 주장을 그냥 하지 않는다.

동일한 평가 데이터에서 비교:

```text
A. Cooker only
B. Cooker + Motion
C. Cooker + Motion + Door
D. Cooker + 여러 Context
```

평가:

```text
False Positive
False Negative
Precision
Recall
F1
```

실제로 좋아진 경우에만:

> “Matter context를 추가했을 때 정확도/오경보가 개선됐다”

라고 주장한다.

실험 전 표현:

> “선택적 Matter context가 오탐 감소와 판단 신뢰도 향상에 기여하는지 검증한다.”

---

# 21. Detection State Manager

Detector가 매 순간 같은 이상 상태를 출력해도 알림을 반복 전송하면 안 된다.

권장 상태:

```text
NORMAL
↓
CHECK_REQUIRED
↓
NOTIFIED
↓
CHECKING
↓
RESOLVED
```

처리할 것:

- 동일 이상 반복
- cooldown
- 연속 N회 확인 후 승격
- 정상 복귀
- 보호자 확인 중
- 처리 완료
- 새 이상 발생

권장 작업명:

`08-detection-state-manager`

---

# 22. Home Assistant Service Layer

HA의 역할:

- Matter 상태 수신
- 밥심 Detection 결과 표시
- History
- Dashboard
- Automation
- 보호자 휴대폰 Push
- Actionable Notification
- 응답 상태 저장

중요:

**Matter 상태 수신 성공 ≠ 보호자 알림 성공**

두 단계를 별도로 검증한다.

---

# 23. 보호자 알림 / 응답

최소 상태:

```text
pending
checking
resolved
```

예:

```text
밥심 — 안부 확인 필요

평소와 다른 밥솥 사용 패턴이 감지되었습니다.

[확인 중]
[안부 확인 완료]
```

중요:

```text
푸시 발송
≠
보호자가 알림을 봄
≠
실제 안부 확인 완료
```

모두 별개 상태로 취급한다.

테스트:

- 같은 Wi-Fi뿐 아니라
- 보호자 폰 Wi-Fi OFF
- LTE/5G 환경에서도 수신/응답 확인

---

# 24. Dashboard

발표에서는 한 화면에서 아래가 보이면 좋다.

```text
[밥솥]
현재 상태
통신 상태
마지막 개방
취사 상태

[개인 기준]
평소 개방 횟수
평소 첫 개방 지연

[현재 특징]
개방 감소
첫 개방 지연
취사-개방 괴리

[밥솥 단독 판정]
status
reason

[Matter Context]
가용 기기
다른 생활 활동
corroborating / conflicting / unavailable

[최종 판단]
확인 필요도
근거

[보호자]
알림 발송
확인 중
완료
```

---

# 25. Simulator

하드웨어 팀을 기다리지 않고 HA/분석/알림을 먼저 완성하기 위해 필요.

가상 버튼/이벤트:

```text
뚜껑 열기
뚜껑 닫기
취사 시작
취사 완료
통신 끊기
통신 복구
외출 시작
외출 종료
```

Optional Matter context도 Simulator 가능:

```text
Motion detected
Door opened
Light on
Context offline
```

최종적으로 실제 Matter entity가 준비되면 Simulator Adapter를 Matter Adapter로 교체한다.

---

# 26. 실제 Matter Integration

최종 통합 단계.

```text
nRF54LM20-DK
→ Matter over Thread
→ Border Router
→ Home Assistant Matter
→ Entity
→ Bapsim Adapter
→ 기존 Detection Pipeline
```

목표는 Matter가 들어와도:

```text
Normalizer 이후 코드는 수정하지 않는 것
```

이다.

---

# 27. Nordic Lesson0 PDF와 역할 경계

첨부된 `Nordic_Lesson0_Matter_DevEnv_Setup.pdf`는 주로 **nRF54LM20-DK Matter Device 개발 환경**에 관한 자료다.

내용:

- nRF Connect for Desktop
- VS Code
- nRF Connect Extension Pack
- nRF Connect SDK / Toolchain
- v3.4.0 LTS 설치
- Quick Start
- Matter Light Bulb / Contact Sensor 샘플
- nRF54LM20-DK target 빌드/Flash
- NFC / QR / Bluetooth commissioning
- Nordic MCP Server

현재 분업 기준:

## 하드웨어/Matter 담당

- Nordic 개발환경 설치
- SDK / Toolchain
- nRF firmware
- GPIO / 센서 연결
- Matter Contact Sensor
- Board build / flash
- Thread Device 동작
- Commissioning 가능한 상태까지

## 사용자 담당

- HA Matter 수신
- Matter entity → Bapsim Adapter
- Detection Core
- Colab calibration
- Optional Matter Context
- Fusion
- 알림
- Dashboard

공동 통합:

- 실제 nRF Matter Device를 HA에 commissioning
- 열림/닫힘 값 방향 확인
- availability 확인
- 재부팅/재연결 테스트

Nordic PDF의 Contact Sensor 샘플은 하드웨어 담당이 뚜껑 센서를 구현할 때 유용하다.

---

# 28. 하드웨어 담당과 반드시 합의할 인터페이스

최소:

```text
lid_open: true | false | unknown
availability: online | offline
observed_at: timestamp
```

가능하면:

```text
cooking_state
cooking_finished_at
```

주의:

Matter Boolean State와 내부 `lid_open` 의미가 반대일 수 있으므로 변환 규칙을 명시한다.

권장 내부 의미:

```text
lid_open = true  → 뚜껑 열림
lid_open = false → 뚜껑 닫힘
```

---

# 29. 권장 코드 구조

```text
bapsim/
│
├─ infrastructure/
│   └─ home_assistant/
│
├─ inputs/
│   ├─ simulator/
│   ├─ smartthings/
│   ├─ xiaomi/
│   └─ matter/
│
├─ domain/
│   ├─ events/
│   │   └─ normalizer/
│   │
│   ├─ sessions/
│   │   └─ cooking_session_builder/
│   │
│   ├─ features/
│   │   └─ cooker_feature_extractor/
│   │
│   ├─ baseline/
│   │   └─ personal_baseline/
│   │
│   └─ detection/
│       ├─ gradual_decline/
│       ├─ cook_open_mismatch/
│       ├─ acute_inactivity/
│       ├─ absence/
│       ├─ data_quality/
│       └─ detection_engine/
│
├─ context/
│   ├─ registry/
│   ├─ adapters/
│   ├─ normalizer/
│   ├─ feature_extractor/
│   ├─ baseline/
│   └─ fusion/
│
├─ workflow/
│   ├─ state_manager/
│   └─ notifications/
│
├─ dashboard/
│
├─ ai/
│   ├─ synthetic_population/
│   ├─ scenarios/
│   ├─ calibration/
│   ├─ ablation/
│   └─ export/
│
└─ tests/
```

---

# 30. Codex 작업 단위

집에서 작업할 때 권장 독립 Task:

```text
01-home-assistant-foundation
02-rice-cooker-simulator
03-cooker-event-normalizer
04-cooking-session-builder
05-behavior-feature-extractor
06-personal-baseline-engine
07-cooker-detection-engine
08-detection-state-manager

09-matter-context-device-registry
10-matter-context-adapters
11-context-feature-extractor
12-context-baseline-engine
13-evidence-fusion-engine

14-caregiver-notification-workflow
15-bapsim-dashboard
16-integration-test-scenarios

17-colab-synthetic-population
18-colab-scenario-generator
19-colab-weight-calibration
20-colab-matter-context-ablation
21-model-config-export

22-matter-adapter
```

---

# 31. 권장 구현 순서

하드웨어를 기다리지 않고:

```text
1. HA Foundation
2. Simulator
3. Normalizer
4. Session Builder
5. Feature Extractor
6. Personal Baseline
7. Rule-based Detection
8. Notification
9. Dashboard
10. Test Scenarios
```

그다음:

```text
11. Colab synthetic population
12. Weight / threshold calibration
13. Matter context abstraction
14. Matter context fusion
15. Ablation evaluation
```

마지막:

```text
16. 실제 nRF Matter Adapter
17. 실제 HA commissioning
18. 실제 센서 → HA → Detection → 보호자 폰 통합
```

---

# 32. MVP와 확장 범위 구분

## 반드시 구현

- Simulator
- 밥솥 공통 이벤트
- Session / Feature
- 개인 baseline
- 최소 Rule Detection
- 통신 장애 분리
- HA 알림
- 보호자 응답
- Dashboard
- 실제 Matter 상태 수신 1개

## 구현하면 강한 차별점

- Colab 범용 weight calibration
- Cooker-only vs optimized weights 비교
- Optional Matter context 1종
- Evidence Fusion
- Context ablation

## 시간이 남으면

- 여러 Matter context type
- Small Neural Network
- 보드 Edge AI
- 제조사 API 실기기 연결
- Multi-Admin
- 원격 밥솥 제어

---

# 33. Matter 대회에서의 설명 논리

단순 설명:

> “Matter로 밥솥 값을 보냅니다.”

는 약하다.

권장 설명:

> “밥심의 핵심 판정은 밥솥만으로도 동작합니다.  
> Matter를 사용하면 특정 제조사 API에 종속되지 않고 표준화된 스마트홈 상태를 받을 수 있으며, 집에 이미 존재하는 다른 Matter 기기가 있을 경우 해당 생활 맥락을 선택적으로 결합해 판단의 오탐을 줄일 수 있는 구조로 확장됩니다.”

즉 Matter의 가치는:

```text
단순 무선 전송
X

다른 제조사 스마트홈 기기와의 상호운용성
+ 선택적 생활 맥락 결합
O
```

---

# 34. 발표/문서에서 과장하지 말 것

아직 실험 전에는 다음 표현을 피한다.

```text
“Matter 기기를 추가하면 정확도가 올라간다.”
```

대신:

```text
“추가 Matter 생활 맥락이 오탐 감소와 판단 신뢰도 향상에 기여하는지 비교 실험한다.”
```

실제로 ablation 결과가 개선된 경우에만 정확도 향상을 주장한다.

마찬가지로 synthetic data에서 좋은 성능이 나와도:

```text
실제 독거가구 건강 위험 탐지 성능
```

으로 확대 해석하지 않는다.

말할 수 있는 것:

```text
정의한 가상 생활 시나리오에서
판정 로직과 선택적 Matter context fusion이
의도한 방식으로 동작했다.
```

---

# 35. Work/Codex에 넘길 때의 요청문

아래 문장을 이 파일과 함께 새 세션에 넣으면 된다.

> 이 파일은 밥심 프로젝트의 Home Assistant/분석/Matter 확장 파트에 대한 최신 인계 컨텍스트다. 먼저 전체 구조와 사용자 담당 범위를 이해해 줘. 사용자의 핵심 개발 범위는 밥솥 데이터를 정규화하고 Session/Feature/Personal Baseline을 만든 뒤, Colab의 가상 시나리오 실험으로 범용 weight와 threshold를 탐색하고, 선택적 Matter context를 결합해 확인 필요도를 계산하는 것이다. 밥솥이 항상 primary이고 다른 Matter 기기는 optional이어야 한다. 다른 기기가 없어도 정상 동작해야 하며, 없는 기기를 0 활동으로 해석하면 안 된다. 하드웨어/Nordic 펌웨어는 별도 담당이며 사용자는 HA 수신 이후의 Adapter/분석/알림/대시보드를 담당한다. 구현할 때 각 모듈을 독립적으로 만들어 실제 Matter 입력이 준비되기 전에는 Simulator로 전체 파이프라인을 완성하고, 마지막에 Matter Adapter만 교체하는 구조를 유지해 줘. 사용자 확정 사항과 아직 실험이 필요한 제안 사항을 구분해서 진행해 줘.
