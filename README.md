# 밥심 · Bapsim

밥솥 사용 패턴의 변화를 관측해 가족의 안부 확인을 돕는 IoT 프로젝트입니다.
외부 센서와 보드의 상태 추정을 Matter로 Home Assistant에 전달하고, 개인별 사용 시간과 사용 공백을 분석해 화면과 알림으로 보여 줍니다.

## 무엇을 보여 주나요?

- **실물 경로:** 뚜껑 개폐·광센서·표면 온도 → Nordic 보드 → Matter over Thread → Nest Hub → Home Assistant.
- **서비스:** 사용 묶음과 개인 기준, 연결 공백 처리, 생활 변화 안내, 보호자의 확인·기간 알림 쉬기·취소.
- **재현 가능한 데모:** 장비 없이 가상 관측부터 알림 요청·사용 재개·재시작 복원까지 실행.

밥솥 개방은 사용 가능 행동의 단서입니다. 식사 완료·섭취량·건강·안전을 확정하지 않습니다. 실제 센서 시험, 합성 평가, 개인화 실생활 성능은 서로 구분합니다.

## 먼저 실행해 보기

Python 3.12 환경에서 저장소 루트에서 실행합니다. 아래 재현은 실제 폰 알림을 보내지 않습니다.

```sh
python -m tools.prepare_demo
```

결과는 Git에서 제외되는 `local-data/demo-kit-<UTC>/`에 저장됩니다. 자세한 순서와 출력 설명은 [가상 데모 안내](docs/DEMO_REHEARSAL.md)를 참고하세요. 전체 테스트는 `python -m unittest discover -s tests`로 실행합니다. 모델 개발 도구의 추가 의존성은 `tools/a07_edge/requirements.txt`, 선택적 MQTT 작업자의 의존성은 `service/requirements.txt`에 있습니다.

## 시스템 구성

```text
밥솥 + 개폐·광·온도 센서
  → nRF54LM20-DK: 관측 처리·운전 상태 추정
  → Matter / Thread → Nest Hub 2세대
  → Home Assistant: 수신·저장·개인 사용 패턴 분석
  → 대시보드 / Companion 앱 알림·응답
```

기본 서비스 실행 방식은 HA 내부 분석입니다. 별도 MQTT 작업자는 선택 사항이며 두 분석 모드를 동시에 운영하지 않습니다. 밥솥 원격 취사 제어와 제조사 API를 사용하지 않습니다.

## 구현과 검증 상태

실제 센서의 live 개폐·표면 온도·LED와 기존 Edge AI 추정 결과를 HA로 전달하는 +21 펌웨어 소스가 포함되어 있습니다. +21 설치 후 실제 보온 상태의 추정 수신을 확인했습니다. 생성 기본값은 test이며, `--live-observations` 또는 `--live-edge`로 전송 범위를 명시합니다. 과거 test 기록을 live로 재분류하지 않습니다.

보드 운전 상태 AI와 HA의 개인 사용 패턴 학습은 별개입니다. 기존 보드 모델을 현장에서 재학습하지 않으며 `model_live_qualified=false`를 유지합니다. 취사·예약 전반의 정확도, 실생활 개인화 성능, 24시간 무누락 및 전체 Matter 등록 정보 복원은 검증 완료를 주장하지 않습니다. 과거 개발 시험의 폰 수신과 현재 구성의 종단간 알림 검증도 구분합니다. [검증 결과와 한계](docs/IMPLEMENTATION_STATUS.md)를 참고하세요.

## HA 보호자 화면 설치

```sh
python -m tools.build_ha_bundle
```

`dist/bapsim_ha_bundle.zip`에는 HA 통합 코드, 보호자 화면 JS, 합성 재생 JSON, 선택 설치용 대시보드·시간 설정 YAML과 `BAPSIM_INSTALL.md`가 들어 있습니다. 기존 HA 설정을 백업한 뒤 ZIP의 안내대로 설정을 병합하고 JS 리소스를 등록하세요. Python 통합을 설치·갱신한 뒤에는 구성 검사와 HA 재시작이 필요합니다. Matter 등록과 입력 매핑, 실제 휴대폰 알림 대상 설정은 별도입니다.

[화면 설치 안내](docs/CAREGIVER_CARD.md)와 [합성 재생 설명](docs/CAREGIVER_REPLAY.md)을 참고하세요. 화면의 합성 재생은 학습된 개인 기준부터 공백·확인 요청·보호자 처리까지 보여주며, 실제 기록을 변경하거나 휴대폰에 발송하지 않습니다.

## 문서 안내

| 읽을 문서 | 내용 |
|---|---|
| [데모 재현](docs/DEMO_REHEARSAL.md) | 장비 없는 실행과 기대 결과 |
| [아키텍처](docs/architecture/시스템_아키텍처.md) | 센서부터 휴대폰까지의 경로와 책임 |
| [하드웨어·배선](docs/hardware/README.md) | 장비, 회로와 관측 한계 |
| [보드 상태 추정](docs/planning/보드_출력과_상태_추정.md) | 전처리·모델·판단 불가 |
| [개인 패턴 모델](docs/개인화_사용패턴_모델.md) | 사용 묶음·개인 기준·학습 제외 |
| [Matter 계약](docs/contracts/Matter_HA_연동_명세.md) | Snapshot, 품질, 버전과 수신 |
| [설치 안내](deploy/README.md) · [서비스](service/README.md) | 실행 모드·설정·배포 |
| [검증 요약](docs/IMPLEMENTATION_STATUS.md) | 결과, 한계와 재현 기준 |

## 저장소 구성과 공개 범위

`firmware/`는 보드, `domain/`은 분석 도메인, `service/`는 HA 수신과 알림, `inputs/`는 원시 입력, `tests/`는 자동 검증, `tools/`는 재현·배포 도구입니다. 입력 어댑터와 시뮬레이터에는 AI 판정을 넣지 않습니다.

개인 실행계획·인터뷰 원문·내부 운영 로그·검토 ZIP은 비공개 작업 자료로 보존하고 공개 문서에서는 제외합니다. 설계 이유와 검증 한계는 기술 문서에 남깁니다. 토큰·실제 설정·개인 관측·HA 백업·등록 정보는 공유하지 않습니다.

프로젝트 자체 코드·문서는 [MIT License](LICENSE)로 제공합니다. 타사 구성요소에는 각자의 라이선스가 적용됩니다. [제3자 고지](THIRD_PARTY_NOTICES.txt)를 참고하세요. 저장소 주소는 유지하며, 공개 준비와 GitHub visibility 전환은 별개입니다.
