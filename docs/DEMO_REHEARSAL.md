# 밥심 출품용 가상 데모 재현

이 데모는 실사용 기록, 보드, Home Assistant 계정, 휴대폰 없이 **소프트웨어 흐름만** 재현한다. 밥솥 사용 관측에서 보호자 알림 요청까지의 코드 동작을 보여주는 자료이며, 실제 식사 여부·건강 상태·푸시 도착 정확도의 증거는 아니다.

## 한 번에 준비하기

Python 3.12와 Git이 설치된 PC에서 저장소 루트로 이동해 실행한다. 이 오프라인 재현에는 Docker와 비밀 토큰이 필요 없다.

```powershell
python -m tools.prepare_demo
```

명령은 `local-data/demo-kit-<UTC시각>/`에 새 폴더를 만들고 다음을 저장한다. 기존 폴더를 덮어쓰지 않는다.

| 파일 | 발표에서 확인할 내용 |
|---|---|
| `DEMO_CARD.md` | 흐름 요약과 가상 검증 한계 |
| `journey.json` | 7일 관측 → 첫 누락 보류 → 두 번째 누락 요청 → 보호자 확인 → 사용 재개 → 재시작 복원 |
| `software-matrix.json` | T08~T13, T17, T20 가상 조건과 결과 |
| `tests.log` | 전체 Python 자동 테스트 실행 기록 |
| `bapsim_ha_bundle.zip` | 같은 코드에서 생성한 HA 설치용 통합 ZIP |
| `manifest.json` | 커밋, 작업 파일 수정 여부, 각 검사 결과, ZIP SHA-256, 증거의 범위 |

특정 경로를 지정하려면 `python -m tools.prepare_demo --output-dir local-data/my-demo-run`을 쓴다. 지정한 폴더가 이미 존재하면 실패한다. 결과 폴더는 Git에서 제외된다. 공유할 때는 폴더 전체를 복사하고 `manifest.json`의 `working_tree_modified`가 `false`인지 확인한다.

## 발표 순서

1. `DEMO_CARD.md`를 열어 가상 입력임을 밝힌다.
2. `journey.json`의 첫 누락 0건과 두 번째 누락 1건을 보여준다. 두 번 연속 확인 후 보호자 요청을 만든다는 정책을 설명한다.
3. 확인 `accepted`, 사용 재개와 재시작 후 상태 `resolved`, 총 요청 2건을 보여준다. 두 번째 요청은 사용 재개 안내다.
4. `software-matrix.json`에서 야간 보류, 외출·연결 문제, 재시도 등 별도 가상 조건이 통과했음을 보여준다.
5. `manifest.json`의 `limits`를 표시한다. 이 실행의 알림 수신자는 가짜 함수이며 HA·MQTT·휴대폰에는 아무것도 전송하지 않는다.

출력의 시뮬레이션 토픽은 **이름을 확인한 것**이며, 브로커로 게시했다는 뜻은 아니다. `bapsim_ha_bundle.zip`도 빌드 산출물이다. 다른 노트북의 HA 설치와 폰 수신은 별도로 시험해야 한다.

## 다른 노트북에서 같은 결과 얻기

공유 브랜치 `service/software-policy-alignment`를 체크아웃한 뒤 위 명령을 실행한다. 테스트가 실패하면 `tests.log`를 확인한다. 코드 버전이 다르면 `manifest.json`의 `commit`부터 비교한다. HA와 Docker까지 로컬로 실행할 때는 [서비스 설정](../service/README.md)과 [통합 실행 설정](../deploy/README.md)을 따른다. `.env`와 HA 토큰은 데모 폴더에 넣지 않는다.
