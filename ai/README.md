# 개인 사용 패턴 Colab 실험

현재 서비스 판정은 `domain/personal_model/engine.py`의 `PersonalUsageModel`을 사용합니다. [밥심 개인 사용 패턴 학습·평가 노트북](밥심_개인사용패턴_학습_Colab.ipynb)은 이 코드를 그대로 포함하므로 Google Colab에서 파일을 열고 **런타임 → 모두 실행**하면 별도 패키지·저장소 인증 없이 재현됩니다. 노트북의 실행 결과와 생성 코드는 `python -m ai.build_personal_usage_colab`으로 다시 만들 수 있습니다.

- 가상 가구마다 초기 21일을 시간 순서대로 학습하고 뒤의 14일을 평가합니다. 기준 확률 후보는 훈련 가구에서만 고른 뒤 다른 가구 시드에서 확인합니다.
- 고정 24시간 미사용 기준과 내부 `check_required` 후보를 비교합니다. 외출·통신 단절은 활동 없음으로 세지 않습니다. 불규칙 간격과 점진적 감소는 현재 모델의 별도 경로로 확인합니다.
- [결과 JSON](personal_usage_result.json)은 **가상 상황의 기능·민감도 실험**입니다. 실제 식사·건강 상태, 실제 생활 오경보율, 휴대폰 푸시 성능을 뜻하지 않습니다. 후보 값은 운영 설정에 자동 적용되지 않습니다.

기존 [가중치 실험 노트북](밥심_가중치_실험_Colab.ipynb)과 `calibration.py`는 이전 `RuleDetector` 비교용 기록입니다. 현재 개인화 모델의 학습 결과로 사용하지 않습니다.

## 실제 기록 평가를 위한 로컬 준비

[평가 기록 규약](../docs/개인화_평가_기록_규약.md)에 가명 관측·별도 확인 라벨의 JSONL 형식과 제외 기준을 적었습니다. `python -m ai.demo_usage_records --output-dir local-data/usage-demo`로 두 가상 가구의 관측·라벨·보고서를 만들고, `python -m ai.usage_record_pipeline --help`로 별도 기록 평가 명령을 확인할 수 있습니다. 이 경로는 로컬 오프라인 평가용이며 Colab 노트북이나 운영 알림 기준을 자동으로 바꾸지 않습니다.

보고서는 모델의 내부 후보(`personal`)와 상태 관리 규칙을 통과한 발송 요청(`notification_request`)을 따로 집계합니다. 가상 날짜별 라벨과 평가 시각은 실생활 확인 자료가 아니므로 보고서 수치를 실제 정확도로 읽지 않습니다.
