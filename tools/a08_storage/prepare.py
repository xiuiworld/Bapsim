"""Add offline logging to an existing generated A08 app; never edit firmware/."""
import argparse
import hashlib
import json
import shutil
from pathlib import Path


def prepare(app):
    app = Path(app).resolve()
    source = app / "src/app_task.cpp"
    text = source.read_text(encoding="utf-8-sig")
    here = Path(__file__).parent
    target = app / "src/baplog"
    target.mkdir(exist_ok=True)
    for name in ("codec.hpp", "runtime.inc"):
        shutil.copy2(here / name, target / name)
    digest = hashlib.sha256()
    for name in ("a08/config.hpp", "a08/model.hpp", "a07/preprocess.hpp"):
        p = app / "src" / name
        if p.exists():
            digest.update(name.encode() + b"\0" + p.read_bytes())
    # Include every generated A08/A07 file: calibration and model identity are
    # immutable per slot even when firmware is updated between two boots.
    for folder in ("a08", "a07"):
        for p in sorted((app / "src" / folder).glob("*.h*")):
            digest.update(p.name.encode() + b"\0" + p.read_bytes())
    fingerprint = digest.digest()[:16]
    (target / "identity.hpp").write_text("#pragma once\nstatic const uint8_t BAPLOG_CONFIG_HASH[16]={" +
                                         ",".join(str(v) for v in fingerprint) + "};\n")
    if '#include "baplog/runtime.inc"' not in text:
        text = text.replace('#include "storage_probe.inc"', '')
        text = text.replace('        storage_probe::push(sample.boot,sample.sequence,sample.ms,sSensors.raw_codes,sSensors.supply_raw,int(sample.lid));', '')
        text = ('#include <cerrno>\n#include <cstdlib>\n#include <zephyr/drivers/flash.h>\n'
                '#include <zephyr/shell/shell.h>\n#include <zephyr/sys/crc.h>\n'
                '#include "baplog/codec.hpp"\n#include "baplog/identity.hpp"\n' + text)
        marker = 'static a08::Sensors sSensors;'
        if text.count(marker) != 1 or text.count('sSensors.read(sample);') != 1:
            raise ValueError("unexpected generated A08 app; no patch applied")
        text = text.replace(marker, marker + '\n#include "baplog/runtime.inc"')
        text = text.replace('sSensors.read(sample);', 'sSensors.read(sample);\n'
                            '        offline_storage::push(sample.boot,sample.sequence,sample.ms,\n'
                            '            sSensors.raw_codes,sSensors.supply_raw,int(sample.lid),sSensors.read_status);')
        source.write_text(text, encoding="utf-8")
    # A08 demo profiles may disable raw collection while preserving the archive
    # reader and all existing flash data. Older generated apps retain their policy.
    config = app / 'src/a08/config.hpp'
    if config.exists() and 'constexpr bool RAW_ARCHIVE_ENABLED' in config.read_text():
        call = '        offline_storage::push(sample.boot,sample.sequence,sample.ms,'
        guarded = '        if (a08::RAW_ARCHIVE_ENABLED) offline_storage::push(sample.boot,sample.sequence,sample.ms,'
        text = text.replace(call, guarded)
        source.write_text(text, encoding='utf-8')
    # This is an additive storage overlay; the base app owns its release version.
    manifest = {"format": 1, "config_hash": fingerprint.hex(), "base": "0x200000", "end": "0x800000",
                "batch_samples": 200, "slot_bytes": 8192, "source_hashes": {
                    name: hashlib.sha256((here / name).read_bytes()).hexdigest() for name in ("codec.hpp", "runtime.inc")}}
    (target / "manifest.json").write_text(json.dumps(manifest, indent=2))
    return manifest


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--app", required=True)
    print(json.dumps(prepare(parser.parse_args().app), indent=2))
