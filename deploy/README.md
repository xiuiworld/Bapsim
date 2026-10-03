# 통합 실행 설정 — A 주도, B 서비스 요건 제공

A의 HA/Matter 환경과 B 서비스·로컬 MQTT 연결을 재현하는 설정 예시와 실행 안내를 둡니다. 실제 비밀값은 .env 등 로컬 파일로 관리하고 설정 키만 .env.example로 공유합니다.

현재 저장소 루트의 `compose.yaml`은 HA 내부 분석 모드이며, `compose.standalone.yaml`을 추가하면 로컬 Mosquitto와 독립 분석 작업자를 함께 실행합니다. 정확한 기동·토큰·브로커 설정은 [서비스 안내](../service/README.md)에 기록했습니다. 가상 발표 자료를 한 번에 만들 때는 [데모 재현 안내](../docs/DEMO_REHEARSAL.md)를 사용합니다. VM·SDK·Docker 이미지 자체와 비밀값은 저장하지 않습니다.
