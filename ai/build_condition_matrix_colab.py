"""Build and execute the self-contained multi-condition Colab experiment."""

from __future__ import annotations

import contextlib
import hashlib
import io
import json
import os
from pathlib import Path
import sys
import tempfile
import types
import xml.etree.ElementTree as ET

from ai.build_personal_usage_colab import code, markdown, source
from ai.condition_matrix_experiment import experiment


ROOT = Path(__file__).resolve().parent
PROJECT = ROOT.parent
TARGET = ROOT / "밥심_다조건_가상학습_Colab.ipynb"
RESULT = ROOT / "condition_matrix_result.json"

RUN = '''result = experiment()
print("훈련 조건:", ", ".join(result["train_conditions"]))
print("미공개 검증 조건:", ", ".join(result["holdout_conditions"]))
print("훈련 조건만으로 선택:", result["selected"], result["selected_settings"])
print("미공개 조건의 현재 설정:", result["holdout_current"])
print("미공개 조건의 선택 후보:", result["holdout_selected"])
print("미공개 조건의 고정 24시간:", result["holdout_fixed_24h"])
for name in result["holdout_conditions"]:
    old = result["by_condition_current"][name]
    new = result["by_condition_selected"][name]
    print(name, "현재/후보 포착", f'{old["recall"]:.1%}', f'{new["recall"]:.1%}',
          "현재/후보 불필요한 발송 요청·가구·21일", old["nuisance_per_home_21d"],
          new["nuisance_per_home_21d"])
'''

CHART = '''from html import escape
from IPython.display import SVG, display

def condition_svg(result):
    labels = {"gradual_drift": "점진적 시각 이동", "weekend_shift": "주말 시각 이동",
              "skip_and_late": "건너뜀 + 늦은 사용"}
    names = result["holdout_conditions"]
    current = result["by_condition_current"]
    selected = result["by_condition_selected"]
    peak = max(1, *(item["nuisance_per_home_21d"] for group in (current, selected)
                    for item in group.values()))
    parts = ['<svg xmlns="http://www.w3.org/2000/svg" width="850" height="350" viewBox="0 0 850 350">',
             '<rect width="850" height="350" fill="white"/>',
             '<text x="22" y="30" font-family="sans-serif" font-size="20" fill="#182430">미공개 조건별 불필요한 발송 요청</text>',
             '<text x="22" y="52" font-family="sans-serif" font-size="12" fill="#536577">가구당 미래 21일 · 외출/단절 제외 · 실제 보호자 푸시 아님</text>']
    for index, name in enumerate(names):
        y = 79 + index * 88
        parts.append(f'<text x="22" y="{y+17}" font-family="sans-serif" font-size="15" fill="#182430">{escape(labels[name])}</text>')
        for offset, label, value, color in ((0, "현재", current[name]["nuisance_per_home_21d"], "#21659f"),
                                            (30, "선택", selected[name]["nuisance_per_home_21d"], "#ac6426")):
            parts.extend([f'<text x="235" y="{y+offset+16}" font-family="sans-serif" font-size="12" fill="#182430">{label}</text>',
                          f'<rect x="280" y="{y+offset}" width="420" height="21" fill="#e9eef2"/>',
                          f'<rect x="280" y="{y+offset}" width="{420*value/peak:.1f}" height="21" fill="{color}"/>',
                          f'<text x="715" y="{y+offset+16}" font-family="sans-serif" font-size="13" fill="#182430">{value:.2f}</text>'])
    parts.append('</svg>')
    return ''.join(parts)

display(SVG(condition_svg(result)))
'''

EXPORT = '''import json
offline_candidate = {
    "status": "offline_synthetic_experiment_only",
    "selected": result["selected"],
    "selected_settings": result["selected_settings"],
    "training": result["training"],
    "holdout_current": result["holdout_current"],
    "holdout_selected": result["holdout_selected"],
    "by_condition_current": result["by_condition_current"],
    "by_condition_selected": result["by_condition_selected"],
    "warning": "Synthetic cooker-use interruptions are not meal or safety ground truth.",
}
with open("condition_matrix_candidate.json", "w", encoding="utf-8") as handle:
    json.dump(offline_candidate, handle, ensure_ascii=False, indent=2)
print("오프라인 실험 결과 저장: condition_matrix_candidate.json")
'''


def notebook(results: dict[str, object]) -> dict[str, object]:
    baseline = results["holdout_current"]
    chosen = results["holdout_selected"]
    model_code = source(PROJECT / "domain/personal_model/engine.py")
    workflow_code = source(PROJECT / "workflow/state_manager.py")
    experiment_code = source(ROOT / "condition_matrix_experiment.py").replace(
        "from workflow import DetectionStateManager\n", "")
    cells = [
        markdown("summary", "# 밥심 다조건 가상 학습 실험\n\n## 요약\n\n"
                 "50~64세·65세 이상 가상 1인 가구의 밥솥 사용 조건을 바꿔, 개인별 모델을 시간 순서대로 학습하고 "
                 "훈련에서 보지 않은 조건으로 설정 후보를 확인합니다. "
                 f"훈련 조건에서 선택한 설정은 **{results['selected']}**입니다. "
                 f"미공개 조건의 누락 포착률은 현재 {baseline['recall']:.1%}, 선택 후보 {chosen['recall']:.1%}; "
                 f"정상일 발송 요청률은 현재 {baseline['false_positive_rate']:.1%}, "
                 f"선택 후보 {chosen['false_positive_rate']:.1%}입니다. "
                 "이 수치는 설계한 밥솥 사용 중단을 맞힌 비율이지 실제 식사·건강 상태의 정확도가 아닙니다. "
                 "실험 설정은 검증 없이 서비스에 적용하지 않습니다.\n"),
        markdown("method", "## 조건과 검증 방법\n\n"
                 "- 한 가구의 과거 21일을 순서대로 관찰하고 다음 21일을 평가합니다. "
                 "가구마다 규칙적인 아침·저녁, 하루 두 번, 격일 사용 중 하나를 가집니다. "
                 "연령·행동 확률은 검증용 가정이며 한국 인구의 비율을 뜻하지 않습니다.\n"
                 "- **훈련 조건 4개:** 안정적 사용, 전반적으로 늦은 사용, 자연스러운 미사용 증가, 3일 간격 조리. "
                 "현재 설정·확률 기준·늦은 사용 유예·간격 기준·연속 확인 횟수를 비교합니다. "
                 "후보는 훈련 가구의 발송 요청에서 `포착률 - 2×정상일 요청률`로만 선택합니다. 이 가중치는 실험 가정입니다.\n"
                 "- **미공개 검증 조건 3개:** 평가 기간의 점진적인 사용 시각 이동, 주말 시각 이동, "
                 "자연스러운 건너뜀과 늦은 사용의 결합. 검증 가구의 난수 시드도 훈련과 다릅니다. "
                 "검증 결과를 보고 후보를 다시 고르지 않습니다.\n"
                 "- 사용 중단 라벨은 모델 출력과 무관하게 시나리오 생성 전에 정합니다. "
                 "외출·연결 단절일은 평가에서 제외합니다. 반복 개방은 사용 묶음으로 처리합니다. "
                 "모델의 `check_required`를 실제 `DetectionStateManager`에 전달해 발송 요청을 집계합니다. "
                 "전송 성공은 가정하며 실제 보호자 푸시 수신은 재현하지 않습니다.\n"
                 "- 조건 생성기와 라벨은 인위적입니다. 실제 생활에서 밥솥 미사용은 곧 결식·위험이 아닙니다. "
                 "합성 집합 크기를 늘려도 현장 성능을 입증할 수 없습니다.\n"),
        markdown("sources", "## 재현 가능한 코드\n\n"
                 f"개인 모델: `domain/personal_model/engine.py` · SHA-256 `{hashlib.sha256(model_code.encode()).hexdigest()}`. "
                 f"알림 상태 관리: `workflow/state_manager.py` · SHA-256 `{hashlib.sha256(workflow_code.encode()).hexdigest()}`. "
                 f"조건 생성기: `ai/condition_matrix_experiment.py` · SHA-256 `{hashlib.sha256(experiment_code.encode()).hexdigest()}`. "
                 "아래 코드가 포함되어 Colab에서 저장소 연결 없이 실행됩니다.\n"),
        code("model-code", model_code),
        code("workflow-code", workflow_code),
        code("experiment-code", experiment_code),
        markdown("results", "## 훈련 선택과 미공개 조건 결과\n\n"
                 "TP/FP/FN/TN은 외출·단절을 제외한 가구·날 기준 발송 요청입니다. 후보는 실험용이며 운영값은 그대로입니다.\n"),
        code("run", RUN),
        code("chart", CHART),
        markdown("interpretation", "## 해석\n\n"
                 "조건별 불필요한 발송 요청이 늘어나는 곳과 놓친 시나리오를 함께 확인해야 합니다. "
                 "선택 후보가 모든 미공개 조건에서 좋아지는 것은 보장되지 않습니다. "
                 "실사용 자료 없이 얻은 성능 수치는 제품 성능 주장으로 사용하지 않습니다.\n"),
        markdown("export", "## 오프라인 결과 내보내기\n\n"
                 "아래 JSON은 Colab 세션에만 저장되며 Home Assistant의 운영 설정을 바꾸지 않습니다.\n"),
        code("export-code", EXPORT),
    ]
    return {"cells": cells, "metadata": {
        "kernelspec": {"display_name": "Python 3", "language": "python", "name": "python3"},
        "language_info": {"name": "python", "version": "3.10"},
        "colab": {"name": TARGET.name, "provenance": []}},
        "nbformat": 4, "nbformat_minor": 5}


def execute(document: dict[str, object], expected: dict[str, object]) -> None:
    shown: list[str] = []
    ipython = types.ModuleType("IPython")
    display_module = types.ModuleType("IPython.display")
    display_module.SVG = lambda value: value
    display_module.display = lambda value: shown.append(value)
    ipython.display = display_module
    saved = {name: sys.modules.get(name) for name in ("IPython", "IPython.display",
                                                       "__notebook__")}
    sys.modules["IPython"] = ipython
    sys.modules["IPython.display"] = display_module
    notebook_module = types.ModuleType("__notebook__")
    sys.modules["__notebook__"] = notebook_module
    prior_cwd = Path.cwd()
    try:
        with tempfile.TemporaryDirectory() as temporary:
            os.chdir(temporary)
            try:
                namespace = notebook_module.__dict__
                number = 0
                for cell in document["cells"]:
                    if cell["cell_type"] != "code":
                        continue
                    number += 1
                    output = io.StringIO()
                    shown.clear()
                    with contextlib.redirect_stdout(output):
                        exec("".join(cell["source"]), namespace)
                    cell["execution_count"] = number
                    if output.getvalue():
                        cell["outputs"].append({"output_type": "stream", "name": "stdout",
                                                "text": output.getvalue().splitlines(keepends=True)})
                    for svg in shown:
                        ET.fromstring(svg)
                        cell["outputs"].append({"output_type": "display_data",
                                                "data": {"image/svg+xml": svg,
                                                         "text/plain": "Synthetic condition comparison"},
                                                "metadata": {}})
                assert namespace["result"] == expected
                assert Path("condition_matrix_candidate.json").exists()
            finally:
                os.chdir(prior_cwd)
    finally:
        for name, previous in saved.items():
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
