"""Build a self-contained Colab notebook from the tested local experiment code."""

from __future__ import annotations

import contextlib
import io
import json
import os
from pathlib import Path
import re
import sys
import tempfile
import types
import xml.etree.ElementTree as ET


ROOT = Path(__file__).resolve().parent
TARGET = ROOT / "밥심_가중치_실험_Colab.ipynb"


def source(name: str) -> str:
    content = (ROOT / name).read_text(encoding="utf-8")
    content = content.split('\nif __name__ == "__main__":')[0]
    content = re.sub(r"^from __future__ import annotations\n", "", content, flags=re.MULTILINE)
    content = re.sub(r"^from (ai|domain)\.[^\n]+\n", "", content, flags=re.MULTILINE)
    return content.strip() + "\n"


def markdown(cell_id: str, content: str) -> dict[str, object]:
    return {"cell_type": "markdown", "id": cell_id, "metadata": {}, "source": content.splitlines(keepends=True)}


def code(cell_id: str, content: str) -> dict[str, object]:
    return {"cell_type": "code", "id": cell_id, "metadata": {}, "execution_count": None,
            "outputs": [], "source": content.splitlines(keepends=True)}


RESULT_CODE = '''calibration_result = experiment()
variants = calibration_result["variants"]
candidate = variants["optimized_cooker"]
print("밥솥 후보 가중치:", candidate["weights"], "기준점:", candidate["threshold"])
print("보류 데이터 시드:", calibration_result["holdout_seeds"])
print("\\n현재 HA 규칙 / 밥솥 후보: 시드별 F1, 오경보율")
for name in ("live_rule", "optimized_cooker"):
    scores = variants[name]["holdouts"]
    print(name, "F1", [row["f1"] for row in scores], "오경보율", [row["false_positive_rate"] for row in scores])
'''


CHART_CODE = '''from html import escape
from IPython.display import SVG, display

labels = {"live_rule": "현재 HA 규칙", "optimized_cooker": "밥솥 후보식"}

def metric_chart(metric, title, axis_max, unit):
    rows = [(labels[name], sum(item[metric] for item in values["holdouts"]) / len(values["holdouts"]))
            for name, values in calibration_result["variants"].items()]
    parts = ['<svg xmlns="http://www.w3.org/2000/svg" width="760" height="310" viewBox="0 0 760 310">',
             '<rect width="760" height="310" fill="white"/>',
             f'<text x="25" y="30" font-size="19" font-family="sans-serif" fill="#17212b">{escape(title)}</text>']
    for index, (label, value) in enumerate(rows):
        y = 65 + index * 54
        width = max(0, min(500, 500 * value / axis_max))
        parts += [f'<text x="25" y="{y+23}" font-size="15" font-family="sans-serif" fill="#17212b">{escape(label)}</text>',
                  f'<rect x="150" y="{y}" width="500" height="28" fill="#edf1f5"/>',
                  f'<rect x="150" y="{y}" width="{width:.2f}" height="28" fill="#2463a6"/>',
                  f'<text x="660" y="{y+21}" font-size="15" font-family="sans-serif" fill="#17212b">{value*100:.1f}%</text>']
    parts += [f'<text x="150" y="291" font-size="12" font-family="sans-serif" fill="#55606b">0</text>',
              f'<text x="620" y="291" font-size="12" font-family="sans-serif" fill="#55606b">{axis_max*100:.0f}% {escape(unit)}</text>',
              '</svg>']
    return ''.join(parts)

f1_svg = metric_chart("f1", "가상 데이터 평균 F1", 1.0, "F1")
fpr_svg = metric_chart("false_positive_rate", "Mean false positive rate (synthetic)", 0.40, "FPR")
display(SVG(f1_svg))
display(SVG(fpr_svg))
'''


EXPORT_CODE = '''model_config_candidate = {
    "schema_version": 1,
    "status": "offline_candidate_only",
    "features": ["open_drop", "delay_growth", "cook_open_mismatch", "inactivity_growth"],
    "weights": candidate["weights"][:4],
    "threshold": candidate["threshold"],
    "train_seed": 11,
    "holdout_seeds": calibration_result["holdout_seeds"],
    "warning": "Synthetic scenarios only. Do not auto-deploy to live alerts.",
}
with open("model_config_candidate.json", "w", encoding="utf-8") as file:
    json.dump(model_config_candidate, file, ensure_ascii=False, indent=2)
print("후보 설정 저장: model_config_candidate.json")
'''


def notebook() -> dict[str, object]:
    cells = [
        markdown("summary", "# 밥심 가중치 실험\n\n이 노트북은 **가상의 생활 상황**으로 밥솥 사용 변화 계산식의 후보 가중치를 찾습니다. 현재 결과는 `뚜껑 열림 감소 0.1 + 첫 개방 지연 0.4 + 취사·개방 불일치 0.1 + 장시간 미개방 0.4`, 기준점 `0.3`입니다. **실제 가구의 건강 위험 예측식이 아니며 Home Assistant의 라이브 규칙을 바꾸지 않습니다.**\n\n위에서 아래로 셀을 실행하면 동일한 결과를 재현합니다. 별도 파일 업로드나 인증은 필요하지 않습니다.\n"),
        markdown("method", "## 실험 방법\n\n정상, 취사 뒤 개방 감소, 점진적 감소, 장시간 미개방, 외출, 통신 장애, 센서 잡음을 **상황으로 먼저 생성**합니다. 정답 라벨은 상황 종류에서 정하며 찾으려는 가중치 공식으로 만들지 않습니다. 학습 시드 11에서 후보를 찾고, 다른 시드 31·47·73과 변동성에서 비교합니다. 통신 장애·외출은 판정 대상에서 제외합니다.\n\n점수의 입력 네 가지는 개인 기준 대비 뚜껑 열림 감소, 첫 개방 지연 증가, 취사·개방 불일치, 마지막 개방 이후 시간 증가를 0~1 범위로 정리한 값입니다. 가중치 후보는 네 값의 합이 1이 되도록 탐색합니다. 기준점도 0.3~0.6에서 함께 탐색하며, F1과 오경보율을 고려해 선택합니다. 다른 Matter 기기 연계는 향후 확장 범위이며 이 노트북에서 구현·평가하지 않습니다.\n"),
        markdown("data", "## 1. 가상 생활 상황 만들기\n\n아래 코드는 프로젝트의 `ai/synthetic.py`를 노트북 안에 포함한 것입니다.\n"),
        code("synthetic", source("synthetic.py")),
        markdown("live-rule", "## 현재 Home Assistant 규칙 불러오기\n\n아래 코드는 프로젝트의 개인 기준 자료형과 `RuleDetector`를 그대로 포함합니다. 가상 비율을 특징 값과 개인 기준으로 바꿔 현재 운영 규칙을 실행합니다.\n"),
        code("baseline-core", source("../domain/baseline/engine.py")),
        code("detection-core", source("../domain/detection/engine.py")),
        markdown("scoring", "## 2. 계산식 후보 찾기\n\n각 비율은 **현재값 ÷ 개인의 평소 중앙값**입니다. `clip(x)`는 값을 0~1 사이로 제한합니다.\n\n- 열림 감소 = `clip(1 − 열림 횟수 비율)`\n- 지연 증가 = `clip((첫 개방 지연 비율 − 1) ÷ 2)`\n- 취사·개방 불일치 = 취사 횟수 비율이 0.75 이상이면서, 열림 횟수 비율이 0.5 이하이거나 지연 비율이 2 이상이면 1, 아니면 0\n- 미개방 시간 증가 = `clip((마지막 개방 이후 시간 비율 − 1) ÷ 2)`\n\n후보 점수는 이 네 값에 각각 가중치를 곱해 더합니다. 가상 데이터에서 찾은 후보는 `0.1×열림 감소 + 0.4×지연 증가 + 0.1×불일치 + 0.4×미개방 시간 증가`이며, `점수 ≥ 0.3`이면 **가상 실험에서** 확인 필요로 분류합니다. 예를 들어 네 입력이 차례로 `0.5, 0.5, 1, 0.5`이면 점수는 `0.55`입니다.\n\n이 실험 코드는 실제 일별 기록 대신 위 비율을 직접 생성합니다. 후보 탐색은 가중치 네 개의 합이 1인 조합과 기준점 0.3~0.6을 비교합니다. 현재 Home Assistant의 보수적 판정 규칙은 별도이며, 이 후보식을 자동 적용하지 않습니다.\n"),
        code("calibration", source("calibration.py")),
        markdown("results", "## 3. 결과 확인\n\n현재 Home Assistant의 `RuleDetector`와 밥솥 데이터만으로 찾은 후보식을 비교합니다. 합성 비율을 기준 개방·취사 2회, 첫 개방 지연 30분, 마지막 개방 12시간의 특징 값으로 바꿔 운영 규칙에 넣습니다. 점진적 감소 시나리오에는 감소 추세를 함께 제공합니다. F1은 높을수록 좋고, 오경보율은 낮을수록 좋습니다. 아래 수치는 가상 시나리오에 한정됩니다.\n"),
        code("run", RESULT_CODE),
        code("charts", CHART_CODE),
        markdown("takeaways", "## 해석과 한계\n\n세 보류 시드에서 밥솥 후보의 F1은 약 0.79~0.81입니다. 이 결과는 정의한 가상 상황에서만 성립합니다. 실제 센서·생활 데이터가 모이면 오경보와 미탐을 다시 평가하고, 그 전에는 후보식을 실제 알림에 배포하지 않습니다. 타 Matter 기기 데이터는 현재 분석에 포함하지 않았습니다.\n"),
        markdown("export", "## 4. 후보 설정 저장\n\n아래 셀은 검토용 JSON만 만듭니다. Home Assistant 설정을 변경하지 않습니다. Colab 왼쪽 파일 목록에서 다운로드할 수 있습니다.\n"),
        code("export-code", EXPORT_CODE),
    ]
    return {
        "cells": cells,
        "metadata": {"kernelspec": {"display_name": "Python 3", "language": "python", "name": "python3"},
                     "language_info": {"name": "python", "version": "3.10"},
                     "colab": {"name": TARGET.name, "provenance": []}},
        "nbformat": 4, "nbformat_minor": 5,
    }


def execute_and_save(document: dict[str, object]) -> None:
    shown: list[str] = []
    ipython = types.ModuleType("IPython")
    display_module = types.ModuleType("IPython.display")
    display_module.SVG = lambda value: value
    display_module.display = lambda value: shown.append(value)
    ipython.display = display_module
    sys.modules["IPython"] = ipython
    sys.modules["IPython.display"] = display_module
    namespace: dict[str, object] = {"__name__": "__main__"}
    original_cwd = Path.cwd()
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
                    cell["outputs"].append({"output_type": "stream", "name": "stdout", "text": output.getvalue().splitlines(keepends=True)})
                for svg in shown:
                    ET.fromstring(svg)
                    cell["outputs"].append({"output_type": "display_data", "data": {"image/svg+xml": svg, "text/plain": "SVG comparison chart"}, "metadata": {}})
            expected = json.loads((ROOT / "calibration_result.json").read_text(encoding="utf-8"))
            assert json.loads(json.dumps(namespace["calibration_result"])) == expected, "Colab calculation differs from local result"
            assert Path("model_config_candidate.json").exists()
        finally:
            os.chdir(original_cwd)


def main() -> None:
    document = notebook()
    execute_and_save(document)
    TARGET.write_text(json.dumps(document, ensure_ascii=False, indent=1), encoding="utf-8")
    saved = json.loads(TARGET.read_text(encoding="utf-8"))
    assert saved["nbformat"] == 4 and all("id" in cell for cell in saved["cells"])
    print(TARGET)


if __name__ == "__main__":
    main()
