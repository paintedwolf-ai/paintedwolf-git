import copy
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
for directory in ("toolchain", "scripts"):
    if str(ROOT / directory) not in sys.path:
        sys.path.insert(0, str(ROOT / directory))

LOCK = json.loads((ROOT / "toolchain/source-lock.json").read_text())


def lock():
    return copy.deepcopy(LOCK)
