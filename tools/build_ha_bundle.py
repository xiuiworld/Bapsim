"""Build a standalone Bapsim custom integration for another HA config directory.

Includes integration sources, caregiver static assets and optional dashboard configuration.
The local HA database, tokens, logs, and simulator configuration are excluded.
"""

from __future__ import annotations

from hashlib import sha256
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile, ZipInfo


PROJECT = Path(__file__).resolve().parents[1]
COMPONENT = PROJECT / "infrastructure/home_assistant/config/custom_components/bapsim"
DEFAULT_OUTPUT = PROJECT / "dist/bapsim_ha_bundle.zip"
ROOT = "custom_components/bapsim"

INSTALL_NOTES = """# Bapsim Home Assistant integration bundle

Back up existing files and merge configuration; do not replace your entire
configuration.yaml. Extract this ZIP into the Home Assistant configuration
directory (usually /config; File editor may show /homeassistant).

Included:
- custom_components/bapsim: integration and its Python runtime
- www/bapsim-caregiver.js: caregiver card
- www/bapsim-caregiver-replay.json: isolated synthetic replay, no real phone send
- optional_config/packages/bapsim_caregiver_routine.yaml
- optional_config/dashboards/bapsim_lab.yaml

Copy the two optional_config files to packages/ and dashboards/ respectively.
Merge these keys into configuration.yaml, preserving existing siblings:

```yaml
bapsim:
homeassistant:
  packages: !include_dir_named packages
lovelace:
  mode: storage
  dashboards:
    bapsim-lab:
      mode: yaml
      title: 밥심
      icon: mdi:rice
      show_in_sidebar: true
      filename: dashboards/bapsim_lab.yaml
```

If packages or dashboards already exist, merge rather than duplicate their keys.
Keep an existing Lovelace resource management mode instead of changing it.
In storage mode, enable Advanced mode in your HA profile if needed, then open
Settings > Dashboards > Resources and register this URL as a JavaScript module:

`@RESOURCE_URL@`

Update the existing entry for this JS when upgrading; do not register it twice.
If resources are already YAML-managed, add/update the same URL with type: module
in your existing lovelace.resources list instead of using the resource UI.
The /local/ URL maps to www/, not to optional_config/.

Run HA's configuration check and restart HA after installing/updating Python
files (also needed when first creating www/). Reload the app/dashboard and open
/bapsim-lab/status. For later JS/JSON-only updates, replace both www files and
update the resource version URL; no backend restart is needed. Exit and restart
an active replay to fetch its new JSON.

The developer simulator is a hidden subview at /bapsim-lab/simulator; its buttons
need the separate simulator package, which is not included. The caregiver's
synthetic record replay uses the included JSON and needs no simulator package.

This bundle does not install Matter Server, commission a board, register input
entities or configure a phone recipient. Follow the project input setup guide;
check real entity values/direction before registering them. Without a verified
input, the caregiver screen may correctly show that observations are unavailable.
UI installation and synthetic replay do not prove real phone delivery.
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
    for name in ("bapsim-caregiver.js", "bapsim-caregiver-replay.json"):
        payload = (PROJECT / "infrastructure/home_assistant/config/www" / name).read_bytes()
        members.append((f"www/{name}", payload))
    # Match the normalized bytes actually written into the deterministic ZIP.
    card = dict(members)["www/bapsim-caregiver.js"].replace(b"\r\n", b"\n")
    resource_url = "/local/bapsim-caregiver.js?v=" + sha256(card).hexdigest()[:12]
    members.append(("BAPSIM_INSTALL.md", INSTALL_NOTES.replace(
        "@RESOURCE_URL@", resource_url).encode("utf-8")))
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
