"""Verify the portable HA bundle imports its own runtime code."""

import asyncio
import importlib
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
from unittest import TestCase
from unittest.mock import patch
from zipfile import ZipFile

from test_ha_service_integration import FakeHass, ha_modules
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
                self.assertFalse(any(".storage" in name or "__pycache__" in name for name in names))
                archive.extractall(root)

            sys.path.insert(0, str(root))
            try:
                with patch.dict(sys.modules, ha_modules({})):
                    integration = importlib.import_module("custom_components.bapsim")
                    self.assertIn(".runtime.domain.", integration.CookerEventNormalizer.__module__)
                    self.assertTrue(asyncio.run(integration.async_setup(FakeHass(), {})))
            finally:
                sys.path.remove(str(root))
                for name in tuple(sys.modules):
                    if name == "custom_components" or name.startswith("custom_components.bapsim"):
                        sys.modules.pop(name, None)
