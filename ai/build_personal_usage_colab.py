"""Generate and execute a self-contained Colab notebook for the active model."""

from __future__ import annotations

import contextlib
import hashlib
import io
import json
import os
from pathlib import Path
import re
import sys
import tempfile
import types
import xml.etree.ElementTree as ET

from ai.target_cohort_experiment import experiment


ROOT = Path(__file__).resolve().parent
PROJECT = ROOT.parent
TARGET = ROOT / "밥심_1인가구_중노년_가상시나리오_Colab.ipynb"
RESULT = ROOT / "target_cohort_result.json"


def source(path: Path) -> str:
    content = path.read_text(encoding="utf-8")
    content = re.sub(r"^from __future__ import annotations\n", "", content, flags=re.MULTILINE)
    content = re.sub(r"^from domain\.personal_model import PersonalUsageModel\n", "", content,
                     flags=re.MULTILINE)
    return content.strip() + "\n"


def markdown(cell_id: str, content: str) -> dict[str, object]:
    return {"cell_type": "markdown", "id": cell_id, "metadata": {},
            "source": content.splitlines(keepends=True)}


def code(cell_id: str, content: str) -> dict[str, object]:
    return {"cell_type": "code", "id": cell_id, "metadata": {}, "execution_count": None,
            "outputs": [], "source": content.splitlines(keepends=True)}


RUN = '''result = experiment()
print("가상 생활 유형:", result["profile_count"], "| 훈련 가구:", len(result["train_seeds"]) * result["profile_count"],
      "| 보류 가구:", len(result["holdout_seeds"]) * result["profile_count"],
      "| 변화 시나리오 가구:", len(result["stress_seeds"]) * result["profile_count"])
print("후보 기준값 (훈련 사용자에서만 선택):", result["selected_threshold"])
print("보류 사용자 개인 모델:", result["holdout_personal"])
print("보류 사용자 고정 24시간 기준:", result["holdout_fixed_24h"])
print("생활 변화 시나리오 개인 모델:", result["stress_personal"])
print("유형별 보류 결과:")
for name, values in result["holdout_by_profile"].items():
    print(name, "포착률", f'{values["recall"]:.1%}', "28일당 불필요한 후보", values["false_alerts_per_household_28d"])
'''


CHART = '''from html import escape
from IPython.display import SVG, display

def comparison_svg(result):
    personal = result["holdout_personal"]
    fixed = result["holdout_fixed_24h"]
    measures = [("보류: 개인 모델", personal), ("보류: 고정 24시간", fixed),
                ("변화: 개인 모델", result["stress_personal"]),
                ("변화: 고정 24시간", result["stress_fixed_24h"])]
    parts = ['<svg xmlns="http://www.w3.org/2000/svg" width="820" height="475" viewBox="0 0 820 475">',
             '<rect width="820" height="475" fill="white"/>',
             '<text x="24" y="30" font-family="sans-serif" font-size="19" fill="#17212b">가상 보류 가구와 생활 변화 가구 비교</text>',
             '<text x="24" y="53" font-family="sans-serif" font-size="12" fill="#52606d">가구마다 미래 28일 · 외출/단절 제외 · 내부 확인 후보, 실제 보호자 알림 아님</text>']
    for index, (name, values) in enumerate(measures):
        y = 78 + index * 88
        parts.append(f'<text x="24" y="{y+17}" font-family="sans-serif" font-size="15" fill="#17212b">{escape(name)}</text>')
        for metric, offset, color, label in (("recall", 0, "#2463a6", "누락 포착"),
                                             ("false_positive_rate", 34, "#ad6321", "정상일 오경보")):
            value = values[metric]
            parts.extend([f'<text x="170" y="{y+offset+16}" font-family="sans-serif" font-size="12" fill="#17212b">{label}</text>',
                          f'<rect x="280" y="{y+offset}" width="420" height="22" fill="#edf1f5"/>',
                          f'<rect x="280" y="{y+offset}" width="{420*value:.1f}" height="22" fill="{color}"/>',
                          f'<text x="715" y="{y+offset+17}" font-family="sans-serif" font-size="13" fill="#17212b">{value*100:.1f}%</text>'])
    parts.append('</svg>')
    return ''.join(parts)

display(SVG(comparison_svg(result)))
'''


PROFILE_CHART = '''from html import escape
from IPython.display import SVG, display

def profile_svg(result):
    parts = ['<svg xmlns="http://www.w3.org/2000/svg" width="820" height="340" viewBox="0 0 820 340">',
             '<rect width="820" height="340" fill="white"/>',
             '<text x="24" y="30" font-family="sans-serif" font-size="19" fill="#17212b">가상 생활 유형별 28일당 불필요한 내부 후보</text>',
             '<text x="24" y="51" font-family="sans-serif" font-size="12" fill="#52606d">보류 가구 · 유형별 4가구 · 값이 낮을수록 보호자 부담 가능성이 낮음</text>']
    profiles = result["holdout_by_profile"]
    scale = max(1, max(item["false_alerts_per_household_28d"] for item in profiles.values()))
    for index, (name, values) in enumerate(profiles.items()):
        y = 67 + index * 32
        value = values["false_alerts_per_household_28d"]
        parts.extend([f'<text x="24" y="{y+17}" font-family="sans-serif" font-size="12" fill="#17212b">{escape(name)}</text>',
                      f'<rect x="225" y="{y}" width="435" height="20" fill="#edf1f5"/>',
                      f'<rect x="225" y="{y}" width="{435*value/scale:.1f}" height="20" fill="#ad6321"/>',
                      f'<text x="675" y="{y+16}" font-family="sans-serif" font-size="12" fill="#17212b">{value:.2f}</text>'])
    parts.append('</svg>')
    return ''.join(parts)

display(SVG(profile_svg(result)))
'''


EXPORT = '''import json

candidate = {
    "schema_version": 1,
    "status": "offline_candidate_only",
    "model": "personal_usage_v1",
    "alert_probability": result["selected_threshold"],
    "train_seeds": result["train_seeds"],
    "holdout_seeds": result["holdout_seeds"],
    "holdout_personal": result["holdout_personal"],
    "warning": "Synthetic behavior only. Review real observations before changing live alerts.",
}
with open("personal_usage_candidate.json", "w", encoding="utf-8") as handle:
    json.dump(candidate, handle, ensure_ascii=False, indent=2)
print("검토용 후보 저장: personal_usage_candidate.json")
'''


def notebook(results: dict[str, object]) -> dict[str, object]:
    personal = results["holdout_personal"]
    fixed = results["holdout_fixed_24h"]
    model_path = PROJECT / "domain/personal_model/engine.py"
    experiment_path = ROOT / "target_cohort_experiment.py"
    model_code = source(model_path)
    experiment_code = source(experiment_path)
    cells = [
        markdown("summary", "# 밥심 1인 가구 중년·노년층 가상 시나리오 실험\n\n"
                 "## 요약\n\n이 노트북은 **현재 서비스의 `PersonalUsageModel` 코드**로 "
                 "1인 가구의 다양한 가상 밥솥 사용 패턴을 시간 순서대로 학습합니다. "
                 "합성 미래 기록에서 내부 확인 필요 판정을 고정 24시간 기준과 비교합니다. "
                 f"선택된 후보 확률 기준은 **{results['selected_threshold']:.2f}**입니다. "
                 f"보류 가구의 누락 포착률: 개인 모델 **{personal['recall']:.1%}**, "
                 f"고정 기준 **{fixed['recall']:.1%}**. 정상일 오경보율: 개인 모델 "
                 f"**{personal['false_positive_rate']:.1%}**, 고정 기준 **{fixed['false_positive_rate']:.1%}**. "
                 f"가상 가구는 총 **{results['profile_count'] * (len(results['train_seeds']) + len(results['holdout_seeds']) + len(results['stress_seeds']))}곳**입니다. "
                 "정해 놓은 가상 행동에 대한 내부 판정 결과이며 실제 식사·건강 상태 정확도나 휴대폰 푸시 성능이 아닙니다.\n"),
        markdown("method", "## 방법과 주요 가정\n\n"
                 "- 50~64세·65세 이상 가상 1인 가구를 8개 생활 유형으로 **균형 있게** 만듭니다. "
                 "아침·점심·저녁 사용, 하루 한 번 사용, 이틀 간격 조리, 주말 시간 이동 등을 포함합니다. "
                 "연령별 유형 배분과 밥솥 사용 확률은 검증용 가정이며 인구 대표 비율이 아닙니다.\n"
                 "- 각 가구의 28일 기록을 먼저 처리하고 뒤의 28일을 시간 순서대로 평가합니다. "
                 "확률 후보는 훈련 가구에서만 선택하고, 다른 가구 시드의 보류 집합과 "
                 "평소보다 늦게 사용하고 자연스러운 미사용이 늘어나는 변화 집합을 별도로 평가합니다. "
                 "미래 개방 시각으로 과거 기준을 만들지 않습니다.\n"
                 "- 정상적인 밥솥 미사용과 의도된 사용 중단을 별도 시나리오로 생성합니다. "
                 "라벨은 **생성 시점에** 정하며 모델 점수로 만들지 않습니다. 두 상황이 관측상 같을 수 있어 "
                 "합성 라벨을 실제 위험의 정답으로 해석할 수 없습니다.\n"
                 "- 외출·단절은 정답 0으로 채우지 않고 평가에서 제외합니다. 고정 비교는 마지막 개방 이후 24시간 초과입니다. "
                 "두 방법 모두 **하루 중 한 번이라도 나온 내부 확인 필요 후보**를 비교하며, 실제 보호자 발송 정책은 별도입니다.\n"
                 "- 가상 집합은 설계한 상황에 편향돼 있습니다. 실제 사용자 기록과 별도 확인 자료가 준비되기 전에는 "
                 "후보를 운영 알림에 적용하지 않습니다.\n"),
        markdown("references", "## 참고 근거와 사용 범위\n\n"
                 "- [2025 통계로 보는 1인가구](https://mods.go.kr/board.es?act=view&bid=10820&list_no=442130&mid=a10301010000): 대상 가구의 규모·연령 분포를 설명합니다.\n"
                 "- [국내 중년 1인 가구 식생활 연구](https://www.e-nrp.org/search.php?code=0161NRP&id=10.4162/nrp.2025.19.2.257&vmode=FULL&where=aview): 결식과 식사 빈도의 다양성을 참고합니다.\n"
                 "- [국내 독거 노년층 식생활 연구](https://pmc.ncbi.nlm.nih.gov/articles/PMC7263895/): 독거 노년층의 결식·식단 차이를 참고합니다.\n"
                 "- [2025 양곡소비량조사](https://mods.go.kr/boardDownload.es?bid=230&list_no=443168&seq=12): 연간 쌀 소비량은 한 끼 양이나 밥솥 개방 수로 변환하지 않습니다.\n"
                 "이 문헌들은 밥솥 개방 시각·횟수의 분포를 제공하지 않습니다. 아래 수치는 문헌 추정치가 아니라 명시적인 합성 가정입니다.\n"),
        markdown("source", "## 1. 현재 운영 모델 코드\n\n"
                 f"출처: `domain/personal_model/engine.py` · SHA-256 `{hashlib.sha256(model_code.encode()).hexdigest()}`. "
                 "노트북에 코드를 포함해 Colab에서 저장소 연결 없이 실행할 수 있습니다.\n"),
        code("model-code", model_code),
        markdown("synthetic", "## 2. 가상 가구 생성과 날짜 순서 평가\n\n"
                 f"출처: `ai/target_cohort_experiment.py` · SHA-256 `{hashlib.sha256(experiment_code.encode()).hexdigest()}`. "
                 "가상 라벨과 판정 코드는 분리돼 있습니다.\n"),
        code("experiment-code", experiment_code),
        markdown("results", "## 3. 후보 선택과 보류 가구 결과\n\n"
                 "`TP/FP/FN/TN`은 외출·단절을 제외한 가구·날 단위입니다. 후보 선택에는 보류 가구 결과를 사용하지 않습니다.\n"),
        code("run", RUN),
        code("chart", CHART),
        code("profile-chart", PROFILE_CHART),
        markdown("interpretation", "## 해석\n\n"
                 "정상적인 미사용과 시나리오상 사용 중단은 밥솥 센서만으로 구분할 수 없는 경우가 있습니다. "
                 "유형별 28일당 불필요한 내부 후보를 함께 봐야 보호자 알림 부담을 이해할 수 있습니다. "
                 "실제 관측과 보호자의 독립적인 확인 라벨이 쌓이면 같은 평가 코드를 실제 가구별 시간 분리 검증에 다시 사용해야 합니다.\n"),
        markdown("export", "## 4. 검토용 후보 내보내기\n\n"
                 "아래 셀은 JSON 파일을 현재 Colab 세션에 저장합니다. Home Assistant 설정이나 실행 중인 모델을 바꾸지 않습니다.\n"),
        code("export-code", EXPORT),
    ]
    return {
        "cells": cells,
        "metadata": {"kernelspec": {"display_name": "Python 3", "language": "python", "name": "python3"},
                     "language_info": {"name": "python", "version": "3.10"},
                     "colab": {"name": TARGET.name, "provenance": []}},
        "nbformat": 4, "nbformat_minor": 5,
    }


def execute(document: dict[str, object], expected: dict[str, object]) -> None:
    shown: list[str] = []
    ipython = types.ModuleType("IPython")
    display_module = types.ModuleType("IPython.display")
    display_module.SVG = lambda value: value
    display_module.display = lambda value: shown.append(value)
    ipython.display = display_module
    saved_modules = {name: sys.modules.get(name) for name in ("IPython", "IPython.display")}
    sys.modules["IPython"] = ipython
    sys.modules["IPython.display"] = display_module
    namespace: dict[str, object] = {"__name__": "__notebook__"}
    prior_cwd = Path.cwd()
    try:
        with tempfile.TemporaryDirectory() as temporary:
            os.chdir(temporary)
            try:
                count = 0
                for cell in document["cells"]:
                    if cell["cell_type"] != "code":
                        continue
                    count += 1
                    output = io.StringIO()
                    shown.clear()
                    with contextlib.redirect_stdout(output):
                        exec("".join(cell["source"]), namespace)
                    cell["execution_count"] = count
                    if output.getvalue():
                        cell["outputs"].append({"output_type": "stream", "name": "stdout",
                                                "text": output.getvalue().splitlines(keepends=True)})
                    for svg in shown:
                        ET.fromstring(svg)
                        cell["outputs"].append({"output_type": "display_data",
                                                "data": {"image/svg+xml": svg,
                                                         "text/plain": "Synthetic holdout comparison"},
                                                "metadata": {}})
                assert namespace["result"] == expected, "embedded Colab result differs from local source"
                assert Path("personal_usage_candidate.json").exists()
            finally:
                os.chdir(prior_cwd)
    finally:
        os.chdir(prior_cwd)
        for name, previous in saved_modules.items():
            if previous is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = previous


def main() -> None:
    results = experiment()
    document = notebook(results)
    execute(document, results)
    TARGET.write_text(json.dumps(document, ensure_ascii=False, indent=1), encoding="utf-8")
    RESULT.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
    saved = json.loads(TARGET.read_text(encoding="utf-8"))
    assert saved["nbformat"] == 4 and all("id" in cell for cell in saved["cells"])
    print(TARGET)


if __name__ == "__main__":
    main()
