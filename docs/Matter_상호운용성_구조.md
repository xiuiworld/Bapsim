# Matter 상호운용성 코드 구조

2026-10-02 기준. 심사에서 보여줄 것은 실물 장치 수만이 아니라 입력원이 늘어나도 밥심의 핵심 분석 코드를 바꾸지 않는 구조다. 아래의 코드 경로는 모의 HA 이벤트로 검증했으며, 실제 Multi-Admin 동작 성공을 뜻하지 않는다.

## 경계

```text
Matter 기기 / 다른 제조사 기기
  → 각 Controller와 Home Assistant의 Matter 통합
  → HA entity
  → 밥심 입력 어댑터
  → 8필드 원시 이벤트 계약
      ├─ 밥솥: bapsim_raw_event → CookerEventNormalizer → 밥솥 분석
      └─ 보조 기기: bapsim_context_raw_event → 향후 별도 정규화·특징·융합
```

밥솥 어댑터는 접점과 취사 상태의 실제 HA 값을 받는다. 센서 부착 방식이나 Matter endpoint가 바뀌어도 원시 입력 매핑만 바꾼다. 공통 경계는 [inputs/base/contracts.py](../inputs/base/contracts.py)이며, 정규화 이후 세션·특징·개인 기준·판정은 입력 기기의 제조사나 Controller를 알 필요가 없다.

`bapsim.register_matter_context`는 선택적 Matter 엔티티를 논리적 `device_id`, HA `entity_id`, `signal`로 등록한다. 예를 들어 `occupancy`, `contact`, `light`, `plug`라는 신호 이름을 사용할 수 있다. 실제 HA 상태 문자열(`on`, `off` 등)을 해석 없이 `bapsim_context_raw_event`로 보낸다. 각 이벤트는 밥솥과 같은 8필드를 가지며 `source=matter`다. `unknown/unavailable`, 엔티티 제거, Matter 출처 상실은 `availability=offline`, `quality=unknown`으로 기록한다. `sensor.bapsim_matter_context`의 `devices` 속성에서 등록·가용성을 확인할 수 있다. 등록값은 HA 재시작 후 복원된다.

예를 들어 HA 개발자 도구의 동작에서 아래처럼 등록한다. ID는 실제 Matter 엔티티로 바꾼다.

```yaml
action: bapsim.register_matter_context
data:
  device_id: motion_01
  entity_id: binary_sensor.example_motion
  signal: occupancy
```

다른 역할의 기기는 별도 `device_id`와 `signal`로 추가한다. `bapsim.unregister_matter_context`에는 제거할 `entity_id`를 준다. 진단 상태 `ready`는 엔티티 값 수신만 뜻하며 물리 센서의 의미 검증은 아니다.

이 경로는 **생활 활동 판정이나 AI를 어댑터에 넣지 않는다.** 보조 기기 이벤트는 밥솥 정규화기로 전달되지 않으며 현재 점수·알림도 바꾸지 않는다. 추가 기기가 없으면 밥솥 단독 경로가 그대로 동작한다. 향후 보조 기기 정규화·가용성 마스크·특징·근거 융합은 [workflow/evidence_boundary.py](../workflow/evidence_boundary.py) 뒤에 별도로 붙인다. 기기 부재와 관측된 활동 0은 구분한다.

## Multi-Admin과 Controller 경계

Multi-Admin은 Matter 기기가 두 Controller의 fabric에 공유되는 프로토콜 기능이다. 밥심은 HA Controller에서 보이는 엔티티를 소비한다. 다른 Controller의 내부 API나 장치 노드 번호를 밥심의 `device_id`로 고정하지 않는다. 따라서 두 번째 Controller를 추가하거나 교체해도 HA 쪽 엔티티가 유지되면 분석 코드는 바뀌지 않는다. HA 엔티티가 바뀌면 해당 등록만 갱신한다. 두 Controller의 실제 동기화는 펌웨어·Matter 스택·Controller 수준의 실물 시험으로 [검증표](하드웨어_심사_실물_검증표.md)에 남긴다.

### Fabric · Multi-Admin · Joint Fabric 구현 위치

| 개념 | Matter에서 맡는 일 | 밥심의 현재 구현 |
| --- | --- | --- |
| Fabric | Controller와 기기가 신뢰 자격증명으로 참여하는 보안 도메인 | HA Matter 통합/서버에 위임. 밥심은 Fabric ID·인증서·Node ID를 저장하거나 발급하지 않음 |
| Multi-Admin | 기존 Fabric의 관리자가 공유 절차를 열어 동일 기기를 다른 Fabric에도 추가 | HA의 공유/페어링 흐름과 기기 Matter 스택에 위임. 밥심의 논리적 `device_id`는 Fabric별 Node ID와 분리되어 있음. 실제 두 Controller 시험은 미실행 |
| Joint Fabric | 여러 승인된 관리자가 하나의 공동 Fabric을 관리 | 현재 밥심과 HA 연동 코드에 구현 없음. 보조 Matter 원시 입력 등록이나 일반 Multi-Admin만으로 Joint Fabric 지원을 주장하지 않음 |

현재 개발 목표는 **Multi-Admin 환경에서도 HA에 들어온 같은 밥솥 상태를 계속 분석하는 것**이다. 밥심이 두 번째 Controller를 만들거나 Joint Fabric 관리자가 될 필요는 없다. Joint Fabric 자체를 심사 성과로 내세우려면 지원하는 Matter 스택·Controller·중앙 Datastore와 실제 공동 Fabric 운영 증거가 별도로 필요하다.

## 코드만으로 확인한 범위

- 밥솥 원시 입력과 선택적 보조 기기 원시 입력은 같은 필드 계약을 사용하고 서로 다른 이벤트 버스로 분리된다.
- 두 개의 서로 다른 Matter 보조 엔티티를 등록해 상태를 기록해도 밥솥 행동 이벤트·판정은 생성되지 않는다.
- Matter 출처가 사라지면 보조 입력을 오프라인으로 기록한다.
- 실물 commissioning, 표준 Cluster 준수, 두 Controller의 Multi-Admin 동기화는 장비 준비 후 검증한다.
