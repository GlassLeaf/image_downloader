"""Audit viewers can read preserved records and the new command envelope."""

import json

import pytest
from tools.render_cli_argument_audit import actual
from tools.render_cli_repair_comparison import error


@pytest.mark.parametrize("layout", ["legacy", "execution", "shutdown", "both"])
def test_audit_error_readers_preserve_old_records_and_both_new_phases(layout):
    first = {"code": "configuration_error", "message": "configuration is invalid"}
    second = {"code": "plugin_error", "message": "plugin operation failed"}
    payload = (
        {"error": first}
        if layout == "legacy"
        else {
            "execution": {"error": first if layout in {"execution", "both"} else None, "result": None},
            "shutdown": {"error": second if layout in {"shutdown", "both"} else None},
        }
    )
    row = {"stdout": json.dumps(payload), "stderr": "", "exit_code": 2, "execution": "command"}
    assert error(row) == (second if layout == "shutdown" else first)
    description = actual(row)
    if layout in {"legacy", "execution", "both"}:
        assert "configuration_error" in description
    if layout in {"shutdown", "both"}:
        assert "plugin_error" in description
