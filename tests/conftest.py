import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "demo"))
sys.path.insert(0, str(ROOT / "tests"))

import bypass  # noqa: E402

BYPASSED = bypass.apply_from_env()


def pytest_report_header(config):
    if BYPASSED:
        return (
            f"AVL_BYPASS: guardrails OFF ({', '.join(sorted(BYPASSED))}) "
            "- a baseline run, not the real suite"
        )
