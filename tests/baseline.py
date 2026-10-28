"""The guardrails-on/off baseline: invariant violations per scenario family.

    python tests/baseline.py           print the table
    python tests/baseline.py --write   rewrite the table in README.md
    python tests/baseline.py --check   fail if README.md's table is stale

Runs tests/test_scenarios.py once per column, each in a fresh process with
AVL_BYPASS set for that column, and with --runxfail so a known open defect
counts as the violation it is rather than as an expected failure.
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
README = ROOT / "README.md"
START, END = "<!-- baseline:start -->", "<!-- baseline:end -->"

COLUMNS = [
    ("On", ""),
    ("FSM off", "fsm"),
    ("Idempotency off", "idempotency"),
    ("Policy off", "policy"),
    ("Log off", "log"),
    ("All off", "all"),
]

# scenario test function -> family row, in suite order
FAMILIES = {
    "test_what_runs_is_what_the_policy_allows": "What the model proposes vs what runs",
    "test_malformed_model_output_never_executes": "Malformed model output",
    "test_every_state_event_pair": "Every state/event pair",
    "test_each_side_effect_runs_once_per_email": "Duplicate deliveries and lost acks",
    "test_crash_restart_retry_runs_once_and_replays": "A crash at each step, then restart",
    "test_tampered_log_does_not_replay": "A tampered log",
    "test_tampered_log_refuses_new_appends": "A tampered log",
}
ROWS = list(dict.fromkeys(FAMILIES.values()))


def run_column(bypass: str) -> tuple[Counter, Counter]:
    """Returns (scenarios, violations) per family for one AVL_BYPASS setting."""
    with tempfile.TemporaryDirectory() as tmp:
        report = Path(tmp) / "junit.xml"
        subprocess.run(
            [
                sys.executable, "-m", "pytest", "tests/test_scenarios.py", "-q",
                "--runxfail", "-p", "no:cacheprovider", f"--junitxml={report}",
            ],
            cwd=ROOT,
            env={**os.environ, "AVL_BYPASS": bypass},
            capture_output=True,
            check=False,
        )
        if not report.exists():
            raise SystemExit(f"AVL_BYPASS={bypass!r}: pytest wrote no report")
        cases = ET.parse(report).getroot().iter("testcase")
        scenarios, violations = Counter(), Counter()
        for case in cases:
            family = FAMILIES[case.get("name").split("[")[0]]
            if case.find("error") is not None or case.find("skipped") is not None:
                raise SystemExit(f"AVL_BYPASS={bypass!r}: {case.get('name')} did not run cleanly")
            scenarios[family] += 1
            violations[family] += case.find("failure") is not None
        return scenarios, violations


def build_table() -> str:
    results = [run_column(bypass) for _, bypass in COLUMNS]
    scenarios = results[0][0]
    if any(r[0] != scenarios for r in results) or sum(scenarios.values()) != 102:
        raise SystemExit(f"scenario counts differ between columns or are not 102: {scenarios}")
    header = "| Scenario family | Scenarios | " + " | ".join(name for name, _ in COLUMNS) + " |"
    lines = [header, "|---|---:|" + "---:|" * len(COLUMNS)]
    for row in ROWS:
        cells = [str(v[row]) for _, v in results]
        lines.append(f"| {row} | {scenarios[row]} | " + " | ".join(cells) + " |")
    totals = [f"**{sum(v.values())}**" for _, v in results]
    lines.append(f"| **Total** | **{sum(scenarios.values())}** | " + " | ".join(totals) + " |")
    return "\n".join(lines)


def readme_block(text: str) -> tuple[int, int]:
    start, end = text.index(START) + len(START), text.index(END)
    return start, end


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--write", action="store_true", help="rewrite the table in README.md")
    mode.add_argument("--check", action="store_true", help="fail if README.md's table is stale")
    args = parser.parse_args()

    table = build_table()
    text = README.read_text()
    start, end = readme_block(text)
    block = f"\n{table}\n"
    if args.write:
        README.write_text(text[:start] + block + text[end:])
    elif args.check and text[start:end] != block:
        print("README.md's baseline table is stale; rebuild it with: python tests/baseline.py --write")
        print(table)
        return 1
    print(table)
    return 0


if __name__ == "__main__":
    sys.exit(main())
