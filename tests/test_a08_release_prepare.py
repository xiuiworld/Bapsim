import tempfile
import unittest
from pathlib import Path

from tools.a08_storage.prepare import prepare


class ReleasePreparation(unittest.TestCase):
    def test_demo_collection_guard_preserves_archive_and_is_idempotent(self):
        with tempfile.TemporaryDirectory() as directory:
            app=Path(directory); (app/'src/a08').mkdir(parents=True)
            (app/'src/a07').mkdir()
            (app/'src/a08/config.hpp').write_text('constexpr bool RAW_ARCHIVE_ENABLED = false;')
            (app/'src/app_task.cpp').write_text('static a08::Sensors sSensors;\nvoid acquire() { sSensors.read(sample); }\n')
            prepare(app); first=(app/'src/app_task.cpp').read_text()
            prepare(app)
            self.assertEqual(first,(app/'src/app_task.cpp').read_text())
            self.assertIn('if (a08::RAW_ARCHIVE_ENABLED) offline_storage::push',first)
            self.assertIn('#include "baplog/runtime.inc"',first)

    def test_storage_overlay_preserves_version_and_is_idempotent(self):
        with tempfile.TemporaryDirectory() as directory:
            app = Path(directory)
            (app/'src/a08').mkdir(parents=True)
            (app/'src/a07').mkdir()
            (app/'src/a08/site_profile.hpp').write_text('timer untrusted')
            (app/'src/a07/a07_model.h').write_text('model masked')
            (app/'src/app_task.cpp').write_text('static a08::Sensors sSensors;\nvoid acquire() { sSensors.read(sample); }\n')
            version = 'VERSION_TWEAK = 16\nEXTRAVERSION = a08-demo-final\n'
            (app/'VERSION').write_text(version)
            first = prepare(app)
            source = (app/'src/app_task.cpp').read_bytes()
            second = prepare(app)
            self.assertEqual((app/'VERSION').read_text(), version)
            self.assertEqual(source, (app/'src/app_task.cpp').read_bytes())
            self.assertEqual(first['config_hash'], second['config_hash'])
            (app/'src/a08/site_profile.hpp').write_text('different calibration')
            self.assertNotEqual(first['config_hash'], prepare(app)['config_hash'])


if __name__ == '__main__':
    unittest.main()
