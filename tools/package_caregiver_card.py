"""Package only the caregiver JS for subsequent screen updates (no Python restart)."""
from pathlib import Path
from zipfile import ZipFile
import hashlib

root = Path(__file__).resolve().parents[1]
payload = (root / 'infrastructure/home_assistant/config/www/bapsim-caregiver.js').read_bytes()
version = hashlib.sha256(payload).hexdigest()[:12]
destination = root / 'tmp/caregiver-card'
destination.mkdir(parents=True, exist_ok=True)
with ZipFile(destination / 'screen-only.zip', 'w') as archive:
    archive.writestr('www/bapsim-caregiver.js', payload)
    archive.write(root / 'infrastructure/home_assistant/config/www/bapsim-caregiver-replay.json',
                  'www/bapsim-caregiver-replay.json')
print('Screen only package:', destination / 'screen-only.zip')
print('Replace both www files from the ZIP, then change the existing HA module URL to:')
print('/local/bapsim-caregiver.js?v=' + version)
print('Reload the dashboard/app frontend. No backend or dashboard YAML included.')
