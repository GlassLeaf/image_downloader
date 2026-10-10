"""Observe the existing history process test without changing product code.

Run from the repository with its development environment. The existing test
owns its child processes and terminates them in its finally block on failure.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

_NODE = "tests/v3/application/test_workflow_history.py::test_independent_processes_merge_under_shared_history_lock"
_STARTUP = """import faulthandler
import os
from pathlib import Path

folder = os.environ.get("HISTORY_DIAGNOSTIC_STACK_DIR")
if folder:
    _stack = (Path(folder) / f"process-{os.getpid()}.stack.txt").open("w", encoding="utf-8")
    faulthandler.enable(file=_stack)
    faulthandler.dump_traceback_later(8, repeat=True, file=_stack)
"""


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output", type=Path, help="new directory for logs and child stacks")
    parser.add_argument("--no-autoload", action="store_true", help="disable automatic pytest plugins")
    args = parser.parse_args()
    repository = Path(__file__).resolve().parents[4]
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=False)
    startup = output / "startup"
    startup.mkdir()
    (startup / "sitecustomize.py").write_text(_STARTUP, encoding="utf-8")
    stacks = output / "stacks"
    stacks.mkdir()
    environment = dict(os.environ)
    environment["HISTORY_DIAGNOSTIC_STACK_DIR"] = str(stacks)
    environment["PYTHONPATH"] = os.pathsep.join(filter(None, [str(startup), environment.get("PYTHONPATH")]))
    if args.no_autoload:
        environment["PYTEST_DISABLE_PLUGIN_AUTOLOAD"] = "1"
    command = [
        sys.executable,
        "-m",
        "pytest",
        "-c",
        str(repository / "pyproject.toml"),
        _NODE,
        "-q",
        "--basetemp=" + str(output / "temporary"),
    ]
    with (output / "pytest.log").open("w", encoding="utf-8") as log:
        result = subprocess.run(command, cwd=repository, env=environment, stdout=log, stderr=subprocess.STDOUT)
    blocked = [path.name for path in stacks.glob("*.stack.txt") if "interprocess_lock.py" in path.read_text()]
    summary = {"pytest_exitcode": result.returncode, "blocked_lock_stacks": blocked, "output": str(output)}
    (output / "summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print(json.dumps(summary, indent=2))
    return result.returncode


if __name__ == "__main__":
    raise SystemExit(main())
