"""Verify the portable HA bundle imports its own runtime code."""

from hashlib import sha256
import json
import inspect
from pathlib import Path
import subprocess
import sys
from tempfile import TemporaryDirectory
from unittest import TestCase
from unittest.mock import patch
from zipfile import ZipFile

from test_ha_service_integration import FakeBus, FakeServices, FakeStates, FakeHass, ha_modules
from tools.build_ha_bundle import build


class HABundleTests(TestCase):
    def test_bundle_loads_without_project_pythonpath(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            archive_path = build(root / "bapsim.zip")
            with ZipFile(archive_path) as archive:
                names = archive.namelist()
                self.assertIn("custom_components/bapsim/runtime/domain/events/normalizer/core.py", names)
                self.assertIn("custom_components/bapsim/runtime/service/observations.py", names)
                self.assertIn("optional_config/packages/bapsim_caregiver_routine.yaml", names)
                self.assertIn("optional_config/dashboards/bapsim_lab.yaml", names)
                self.assertIn("custom_components/bapsim/runtime/workflow/notification_controls.py", names)
                self.assertIn("custom_components/bapsim/runtime/workflow/notification_policy.py", names)
                self.assertIn("custom_components/bapsim/runtime/service/ha_notification_transport.py", names)
                self.assertIn("custom_components/bapsim/runtime/service/ha_notification_flow.py", names)
                for module in ("ha_dashboard", "ha_mqtt_output", "ha_matter_inputs", "ha_journal",
                               "ha_analysis_flow", "ha_cooker_events", "ha_periodic",
                               "ha_mqtt_subscriptions", "ha_caregiver_commands",
                               "ha_service_registry"):
                    self.assertIn(f"custom_components/bapsim/runtime/service/{module}.py", names)
                self.assertFalse(any(".storage" in name or "__pycache__" in name for name in names))
                archive.extractall(root)

            # A fresh isolated interpreter has neither repository paths nor cached domain modules.
            probe = "import asyncio, importlib, importlib.util, sys, types\nfrom datetime import datetime, timezone\n"
            probe += "\n".join(inspect.getsource(item) for item in
                               (FakeBus, FakeServices, FakeStates, FakeHass, ha_modules))
            probe += '''
sys.path.insert(0, sys.argv[1])
assert importlib.util.find_spec("domain") is None
sys.modules.update(ha_modules({}))
integration = importlib.import_module("custom_components.bapsim")
assert ".runtime.domain." in integration.CookerEventNormalizer.__module__
assert asyncio.run(integration.async_setup(FakeHass(), {}))
assert "domain" not in sys.modules
print("bundle import and async_setup passed")
'''
            result = subprocess.run([sys.executable, "-I", "-S", "-c", probe, str(root)],
                                    cwd=root, text=True, capture_output=True, timeout=30)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


    def test_caregiver_assets_resource_version_and_replay_are_self_contained(self):
        project = Path(__file__).resolve().parents[1]
        with TemporaryDirectory() as directory:
            with ZipFile(build(Path(directory) / "bundle.zip")) as archive:
                assets = {name for name in archive.namelist() if name.startswith("www/")}
                self.assertEqual(assets, {"www/bapsim-caregiver.js", "www/bapsim-caregiver-replay.json"})
                for name in assets:
                    source = project / "infrastructure/home_assistant/config" / name
                    self.assertEqual(archive.read(name), source.read_bytes().replace(b"\r\n", b"\n"))
                card = archive.read("www/bapsim-caregiver.js")
                notes = archive.read("BAPSIM_INSTALL.md").decode("utf-8")
                self.assertIn("/local/bapsim-caregiver.js?v=" + sha256(card).hexdigest()[:12], notes)
                self.assertNotIn("@RESOURCE_URL@", notes)
                replay = json.loads(archive.read("www/bapsim-caregiver-replay.json"))
                self.assertFalse(replay["real_push_sent"])
                self.assertTrue(all(frame["states"]["sensor.bapsim_output_state"]["attributes"]["source"]
                                    == "simulator" for frame in replay["frames"]))
                self.assertFalse(any(name.startswith(("local-data/", "output/", "firmware/"))
                                     or name.endswith(("secrets.yaml", ".env")) for name in archive.namelist()))
                self.assertNotIn("configuration.yaml", archive.namelist())

    def test_bundle_is_identical_for_lf_and_crlf_sources(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            with patch("tools.build_ha_bundle.bundle_members", return_value=[("example.py", b"a\nb\n")]):
                first = build(root / "lf.zip").read_bytes()
            with patch("tools.build_ha_bundle.bundle_members", return_value=[("example.py", b"a\r\nb\r\n")]):
                second = build(root / "crlf.zip").read_bytes()
            self.assertEqual(first, second)
            with ZipFile(root / "lf.zip") as archive:
                self.assertEqual(archive.getinfo("example.py").create_system, 3)
