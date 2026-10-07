"""Verify the portable HA bundle imports its own runtime code."""

import inspect
from pathlib import Path
import subprocess
import sys
from tempfile import TemporaryDirectory
from unittest import TestCase
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
