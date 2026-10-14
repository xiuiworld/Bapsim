# HA 보호자 화면

`infrastructure/home_assistant/config/www/bapsim-caregiver.js`는 외부 프레임워크 없이 실행하는 `custom:bapsim-caregiver` 카드다. 일상·확인 기록·설정과 내부 입력 폼을 제공한다.

`python -m tools.build_ha_bundle`로 `dist/bapsim_ha_bundle.zip`을 만든다. 통합 Python 코드·두 정적 파일·선택 설치 YAML이 포함되며, ZIP의 `BAPSIM_INSTALL.md`에 실제 포함된 JS 해시에 맞는 리소스 URL과 설정 병합 예시가 있다.

수동 설치 시 JS와 합성 재생 JSON을 HA의 `/config/www/`에 복사하고 대시보드 리소스에 `/local/bapsim-caregiver.js?v=<파일 해시>`를 JavaScript 모듈로 한 번 등록한다. 저장소의 configuration.yaml·대시보드 YAML 및 bapsim Python 구성요소도 설치한다. 기존 설정은 병합하고 덮어쓰지 않는다. 최초 www 폴더 활성화와 Python 변경에는 HA 재시작이 필요할 수 있다. JS·JSON만 바꾸면 리소스 버전 갱신과 화면 새로고침으로 반영한다.

`python tools/package_caregiver_card.py`는 두 정적 파일을 로컬 ZIP으로 묶고 리소스 URL을 출력한다. ZIP·운영 설정·인증정보는 Git에 넣지 않는다.

현재 관측은 출처·품질·유효기간으로 검사하고 분석 준비 여부와 별도로 표시한다. 보호자 응답은 사건 ID와 장치·출처 검증을 유지한다. 사용 재개와 보호자 확인 완료는 별도 종료 사유다. 알림 요청·HA 수락은 휴대폰 도착 증거가 아니다.

검증: `node tests/test_caregiver_card.mjs`, `python -m unittest discover -s tests -p test_caregiver_dashboard.py`. 로컬 검증과 실제 HA 배포·휴대폰 수신은 별도다.
