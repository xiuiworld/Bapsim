"""Create a review-only B source archive from an explicit allowlist."""

from __future__ import annotations

import argparse
from hashlib import sha256
import json
from pathlib import Path
import re
import subprocess
from zipfile import ZIP_DEFLATED, ZipFile, ZipInfo


ROOT = Path(__file__).resolve().parents[1]
PREFIXES = ("ai/", "domain/", "inputs/", "service/", "workflow/", "tests/",
            "infrastructure/home_assistant/config/custom_components/bapsim/")
EXCLUDED_PREFIXES = ("firmware/", "tools/a05_contract/", "tools/a07_edge/")
EXACT = {"README.md", "CONTRIBUTING.md", ".env.example", "compose.yaml",
         "compose.standalone.yaml", "infrastructure/home_assistant/config/dashboards/bapsim_lab.yaml",
         "docs/contracts/AB_데이터_계약.md", "docs/contracts/분석결과_HA_휴대폰_출력계약.md",
         "docs/개인화_평가_기록_규약.md", "docs/개인화_사용패턴_모델.md",
         "docs/planning/B_개인_실행계획.md", "docs/planning/B_배포_보존_복원_안내.md",
         "docs/planning/B_잔여검증_실행표_2026-10-07.md",
         "tools/compare_auxiliary_scenarios.py"}
SECRET_PATTERNS = tuple(re.compile(pattern) for pattern in (
    rb"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----",
    rb"ghp_[A-Za-z0-9]{20,}", rb"sk-[A-Za-z0-9]{20,}", rb"AIza[A-Za-z0-9_-]{25,}"))
NOTICE = ("# B source candidate — review only\n\n"
          "The project has no root LICENSE/NOTICE as of this snapshot. Choose and review "
          "the project's distribution license before publication.\n\n"
          "The optional worker depends on paho-mqtt==2.1.0. Its versioned LICENSE.txt "
          "declares EPL-2.0 or EDL-1.0: "
          "https://github.com/eclipse-paho/paho.mqtt.python/blob/v2.1.0/LICENSE.txt\n\n"
          "This archive excludes firmware, local data, credentials, generated deployment ZIPs, "
          "and unrelated working-tree files. Recheck all contents before publication.\n")


def included(path: str) -> bool:
    if path.startswith(EXCLUDED_PREFIXES):
        return False
    return path in EXACT or path.startswith(PREFIXES) or (
        path.startswith("tools/") and path.count("/") == 1 and path.endswith(".py"))


def build(output: Path) -> dict[str, object]:
    tracked = subprocess.check_output(["git", "ls-files", "-z"], cwd=ROOT).decode("utf-8").split("\0")
    names = {path for path in tracked if path and included(path)}
    names.update(path for path in EXACT if (ROOT / path).is_file())
    names.add("tools/build_b_source_candidate.py")
    files = []
    data = {}
    for name in sorted(names):
        path = ROOT / name
        if not path.is_file() or path.stat().st_size > 2_000_000:
            raise ValueError(f"missing or oversized source file: {name}")
        content = path.read_bytes()
        if any(pattern.search(content) for pattern in SECRET_PATTERNS):
            raise ValueError(f"candidate secret in {name}")
        data[name] = content
        files.append({"path": name, "bytes": len(content), "sha256": sha256(content).hexdigest()})
    manifest = {"kind": "review_only_b_source_candidate", "source_commit": subprocess.check_output(
        ["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip(), "files": files}
    data["RELEASE_CANDIDATE_NOTICE.md"] = NOTICE.encode("utf-8")
    data["SOURCE_MANIFEST.json"] = json.dumps(manifest, ensure_ascii=False, indent=2).encode("utf-8")
    output.parent.mkdir(parents=True, exist_ok=True)
    with ZipFile(output, "w", ZIP_DEFLATED) as archive:
        for name, content in sorted(data.items()):
            item = ZipInfo(name, (1980, 1, 1, 0, 0, 0))
            item.compress_type = ZIP_DEFLATED
            archive.writestr(item, content)
    return {"path": str(output), "files": len(files), "bytes": output.stat().st_size,
            "sha256": sha256(output.read_bytes()).hexdigest()}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=ROOT / "dist" / "bapsim_b_source_candidate.zip")
    result = build(parser.parse_args().output)
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
