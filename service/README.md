# 분석 서비스 — B

현재 `service/`에는 정규화 관측·서비스 기록의 날짜별 보존, MQTT 상태 메시지 생성, 시뮬레이션 전용 보조 활동 보류 코드가 있습니다. 실행 호스트는 현재 HA 커스텀 통합이며 `python -m tools.build_ha_bundle`로 이 모듈을 함께 묶습니다. 독립 Docker 서비스 이미지는 아직 만들지 않았습니다.

- `observations.py`: 관측 90일, 서비스 기록 180일 파티션 보존. HA 설정 디렉터리의 `bapsim_observations/`, `bapsim_records/` 사용.
- `output.py`: 버전·revision·180초 유효기간이 있는 결과 상태. 실물과 시뮬레이션 MQTT 토픽 분리.
- `auxiliary.py`: 가상 보조 활동이 있을 때 시뮬레이션 안내 후보를 30분씩, 최대 2시간 보류. 실물 Matter 판단에는 적용하지 않음.

HA에서 MQTT 통합과 브로커를 설정하면 통합이 `mqtt.publish`로 상태·discovery를 게시합니다. 브로커가 없을 때는 `sensor.bapsim_mqtt_output`에 `broker_unavailable`을 표시합니다. 실제 브로커·폰·실물 입력의 통합시험은 별도입니다.

[분석 입력](../docs/contracts/AB_데이터_계약.md) · [정책](../docs/planning/분석_알림_정책.md) · [출력/조작](../docs/contracts/분석결과_HA_휴대폰_출력계약.md)
