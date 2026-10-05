import json
import sys
from pathlib import Path

ROOT = next(p for p in Path(__file__).resolve().parents if (p / 'AGENTS.md').is_file())
sys.path.insert(0, str(ROOT))

if __name__ == '__main__':
    from ai.in_scope_adaptation_holdout import experiment
    result = experiment()
    Path(__file__).with_name('holdout-rerun.json').write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps(result['decisions'], ensure_ascii=False))
