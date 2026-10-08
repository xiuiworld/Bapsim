"""Build a standalone Bapsim custom integration for another HA config directory.

Only source code and the integration manifest/service description are packaged.
The local HA database, tokens, logs, and simulator configuration are excluded.
"""

from __future__ import annotations

from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile, ZipInfo


PROJECT = Path(__file__).resolve().parents[1]
COMPONENT = PROJECT / "infrastructure/home_assistant/config/custom_components/bapsim"
DEFAULT_OUTPUT = PROJECT / "dist/bapsim_ha_bundle.zip"
ROOT = "custom_components/bapsim"

INSTALL_NOTES = """# Bapsim Home Assistant integration bundle

Extract this ZIP into the Home Assistant configuration directory. It contains
`custom_components/bapsim` with its Python runtime code. Add `bapsim:` to
`configuration.yaml` if it is not already present, then run Home Assistant's
configuration check and restart Home Assistant.

For the caregiver dashboard, copy the two files under optional_config into
the same relative locations in the Home Assistant configuration directory:
packages/bapsim_caregiver_routine.yaml and dashboards/bapsim_lab.yaml.
Add 'homeassistant: packages: !include_dir_named packages' and a YAML Lovelace
dashboard with URL path bapsim-lab pointing to dashboards/bapsim_lab.yaml if
your configuration does not already have them. Check the configuration and
restart Home Assistant. The dashboard includes a local simulator tab; its
buttons require the separate simulator package and are not needed for real use.

This bundle does not install Matter Server or commission the Nordic board.
Register real Matter entities only after checking their actual values and
direction as described in the project guide.
"""


def bundle_members() -> list[tuple[str, bytes]]:
    members = [(f"{ROOT}/{name}", (COMPONENT / name).read_bytes())
               for name in ("__init__.py", "manifest.json", "services.yaml")]
    members.append((f"{ROOT}/runtime/__init__.py", b""))
    for package in ("domain", "inputs", "workflow", "service"):
        for path in sorted((PROJECT / package).rglob("*.py")):
            members.append((f"{ROOT}/runtime/{path.relative_to(PROJECT).as_posix()}", path.read_bytes()))
    for source, target in (
        ("infrastructure/home_assistant/config/packages/bapsim_caregiver_routine.yaml",
         "optional_config/packages/bapsim_caregiver_routine.yaml"),
        ("infrastructure/home_assistant/config/dashboards/bapsim_lab.yaml",
         "optional_config/dashboards/bapsim_lab.yaml"),
    ):
        members.append((target, (PROJECT / source).read_bytes()))
    members.append(("BAPSIM_INSTALL.md", INSTALL_NOTES.encode("utf-8")))
    return sorted(members)


def build(output: Path = DEFAULT_OUTPUT) -> Path:
    output = output.resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    with ZipFile(output, "w") as archive:
        for name, data in bundle_members():
            entry = ZipInfo(name, date_time=(2026, 10, 2, 0, 0, 0))
            entry.create_system = 3
            entry.compress_type = ZIP_DEFLATED
            entry.external_attr = 0o644 << 16
            archive.writestr(entry, data.replace(b"\r\n", b"\n"))
    return output


if __name__ == "__main__":
    print(build())
