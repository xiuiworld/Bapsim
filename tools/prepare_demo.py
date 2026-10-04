"""Create a reproducible, simulation-only Bapsim demonstration kit."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import subprocess
import sys
from tempfile import TemporaryDirectory

from tools.build_ha_bundle import build as build_ha_bundle
from tools.run_software_matrix import run as run_software_matrix
from tools.simulate_notification_flow import replay


ROOT = Path(__file__).resolve().parents[1]


def _write_json(path: Path, payload: dict[str, object]) -> None:
    with path.open("x", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2)
        handle.write("\n")


def _git(*args: str) -> str:
    return subprocess.check_output(["git", *args], cwd=ROOT, text=True).strip()


def prepare(output_dir: Path) -> dict[str, object]:
    """Write a new kit without modifying existing evidence or local HA state."""
    output_dir = output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=False)

    test_result = subprocess.run(
        [sys.executable, "-m", "unittest", "discover", "-s", "tests", "-q"],
        cwd=ROOT, capture_output=True, text=True, check=False,
    )
    test_log = test_result.stdout + test_result.stderr
    (output_dir / "tests.log").write_text(test_log, encoding="utf-8")
    if test_result.returncode:
        raise RuntimeError(f"automated tests failed; inspect {output_dir / 'tests.log'}")
    test_count_match = re.search(r"Ran (\d+) tests? in", test_log)
    if test_count_match is None or int(test_count_match.group(1)) == 0:
        raise RuntimeError(f"automated test count missing; inspect {output_dir / 'tests.log'}")

    with TemporaryDirectory(prefix="bapsim-demo-state-") as directory:
        journey = replay(Path(directory) / "state.json")
    _write_json(output_dir / "journey.json", journey)
    expected_journey = {
        "learned_days": 7,
        "first_gap_requests": 0,
        "second_gap_requests": 1,
        "acknowledgement": "accepted",
        "after_resumed_use": "resolved",
        "after_restart": "resolved",
        "notification_requests": 2,
        "real_push_sent": False,
    }
    if any(journey.get(key) != value for key, value in expected_journey.items()):
        raise RuntimeError(f"caregiver journey changed; inspect {output_dir / 'journey.json'}")

    matrix = run_software_matrix(output_dir / "software-matrix.json")
    if matrix["overall"] != "passed":
        raise RuntimeError(f"synthetic scenario matrix failed; inspect {output_dir / 'software-matrix.json'}")

    bundle = build_ha_bundle(output_dir / "bapsim_ha_bundle.zip")
    bundle_sha256 = hashlib.sha256(bundle.read_bytes()).hexdigest()
    manifest = {
        "schema_version": 1,
        "mode": "simulation",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "commit": _git("rev-parse", "--short", "HEAD"),
        "working_tree_modified": bool(_git("status", "--porcelain")),
        "checks": {"automated_tests": "passed",
                   "tests_run": int(test_count_match.group(1)),
                   "synthetic_matrix": matrix["overall"],
                   "caregiver_journey": "passed"},
        "artifacts": {
            "journey": "journey.json", "scenario_matrix": "software-matrix.json",
            "test_log": "tests.log", "ha_bundle": "bapsim_ha_bundle.zip",
            "ha_bundle_sha256": bundle_sha256,
        },
        "limits": [
            "All observations and labels are synthetic.",
            "The journey uses a fake notification receiver; no HA, MQTT, or phone delivery is demonstrated.",
            "The HA ZIP is a code package, not proof of hardware integration or installation on another laptop.",
        ],
    }
    _write_json(output_dir / "manifest.json", manifest)
    lines = [
        "# 밥심 가상 데모 결과", "",
        "이 폴더는 합성 입력만으로 생성했습니다. 실사용·실기기·실제 휴대폰 수신 결과가 아닙니다.", "",
        f"- 코드 커밋: `{manifest['commit']}`",
        f"- 작업 파일 수정 상태: `{manifest['working_tree_modified']}`",
        f"- 자동 테스트: {manifest['checks']['tests_run']}개 통과 (`tests.log`)",
        "- 가상 시나리오 묶음: 통과 (`software-matrix.json`)", "",
        "| 시연 단계 | 확인한 결과 |", "|---|---|",
        f"| 초기 관측 | {journey['learned_days']}일간 가상 밥솥 사용 기록 |",
        f"| 첫 사용 누락 | 알림 요청 {journey['first_gap_requests']}건 |",
        f"| 두 번째 사용 누락 | 알림 요청 {journey['second_gap_requests']}건 |",
        f"| 보호자 확인 | {journey['acknowledgement']} |",
        f"| 사용 재개 | {journey['after_resumed_use']} |",
        f"| 재시작 후 | {journey['after_restart']}, 총 요청 {journey['notification_requests']}건 |",
        "", f"시뮬레이션 상태 토픽: `{journey['simulation_state_topic']}`", "",
        "`bapsim_ha_bundle.zip`은 설치용 코드이며, 이 재생에서는 HA에 설치하거나 전송하지 않았습니다.",
        "발표 때는 `journey.json`과 `software-matrix.json`을 함께 열어 결과와 한계를 보여주세요.", "",
    ]
    (output_dir / "DEMO_CARD.md").write_text("\n".join(lines), encoding="utf-8")
    return manifest


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, help="New directory for all generated evidence")
    args = parser.parse_args()
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    output_dir = args.output_dir or ROOT / "local-data" / f"demo-kit-{stamp}"
    manifest = prepare(output_dir)
    print(f"{output_dir.resolve()}: {manifest['checks']['caregiver_journey']}")
    print("Open DEMO_CARD.md for the walkthrough and manifest.json for scope and file hashes.")


if __name__ == "__main__":
    main()
