# 분석 서비스 — B

`service/`에는 정규화 관측·서비스 기록의 날짜별 보존, MQTT 상태 메시지 생성, 시뮬레이션 전용 보조 활동 보류 코드가 있습니다. 기본 실행 호스트는 HA 커스텀 통합이며 `python -m tools.build_ha_bundle`로 함께 묶습니다. 선택적으로 `compose.standalone.yaml`을 적용하면 HA가 정규화 이벤트만 MQTT로 보내고, 별도 Python 작업자가 판정·상태 게시·알림 요청을 담당합니다.

- `observations.py`: 관측 90일, 서비스 기록 180일 파티션 보존. HA 설정 디렉터리의 `bapsim_observations/`, `bapsim_records/` 사용.
- `output.py`: 버전·revision·180초 유효기간이 있는 결과 상태. 실물과 시뮬레이션 MQTT 토픽 분리.
- `auxiliary.py`: 가상 보조 활동이 있을 때 시뮬레이션 안내 후보를 30분씩, 최대 2시간 보류. 실물 Matter 판단에는 적용하지 않음.

HA에서 MQTT 통합과 브로커를 설정하면 기본 모드의 통합이 `mqtt.publish`로 상태·discovery를 게시합니다. 브로커가 없을 때는 `sensor.bapsim_mqtt_output`에 `broker_unavailable`을 표시합니다.

## 로컬 독립 서비스 준비

1. Docker Desktop을 실행해 `docker info`에서 Server 버전이 출력되는지 확인합니다. Desktop 시작 시 Windows 권한 승인이나 첫 실행 동의가 필요할 수 있습니다.
2. 저장소 루트에서 `.env.example`을 `.env`로 복사합니다. HA를 처음 실행할 때는 토큰을 비워 두어도 되지만, 알림 전달 전에 HA 사용자 프로필에서 장기 액세스 토큰을 만든 뒤 `.env`의 `BAPSIM_HA_TOKEN`에 넣습니다. 등록된 휴대폰 알림 서비스가 있으면 `BAPSIM_NOTIFY_SERVICE=mobile_app_...`을 지정합니다. `.env`는 Git 추적 대상이 아닙니다.
3. `docker compose -f compose.yaml -f compose.standalone.yaml up -d --build`로 HA, 로컬 Mosquitto, 작업자를 실행합니다.
4. `http://localhost:8123`에서 HA 초기 설정 후 **설정 → 기기 및 서비스 → 통합 추가 → MQTT**를 선택하고 브로커 호스트 `mosquitto`, 포트 `1883`을 입력합니다. HA 컨테이너 안에서 `localhost`는 브로커가 아닙니다.
5. HA를 재시작한 뒤 `docker compose -f compose.yaml -f compose.standalone.yaml logs -f bapsim-worker`로 구독·상태를 확인합니다. `sensor.bapsim_household_01_summary`는 실제 입력이나 시뮬레이터 이벤트가 들어와야 값을 표시합니다.

이 Mosquitto 설정은 **한 PC의 개발 시험용**으로 외부 포트를 `127.0.0.1`에만 묶고 익명 접속을 허용합니다. 실제 가정망에 배포할 때는 브로커 인증과 접근 제어를 구성하고 이 설정을 그대로 사용하지 마세요. 실물 Matter 입력, 폰 푸시 도착, 원격 접속 시험은 실제 장치와 계정이 준비된 뒤 수행합니다.

기본 HA 내부 판정을 계속 쓸 때는 `docker compose up -d`만 실행합니다. 두 모드를 동시에 띄우지 마세요.

[분석 입력](../docs/contracts/AB_데이터_계약.md) · [정책](../docs/planning/분석_알림_정책.md) · [출력/조작](../docs/contracts/분석결과_HA_휴대폰_출력계약.md)
