import json
import subprocess
import textwrap
from pathlib import Path

import pytest


def _assert_exact_read_permissions(text: str) -> None:
    lines = text.splitlines()
    declarations = [
        (index, line)
        for index, line in enumerate(lines)
        if line.strip().startswith("permissions:")
    ]
    assert len(declarations) == 1
    start, declaration = declarations[0]
    assert declaration == "permissions:"

    entries = []
    for line in lines[start + 1 :]:
        if line and not line[0].isspace():
            break
        if line.strip():
            entries.append(line)
    assert entries == ["  contents: read"]


def test_offline_ci_is_credential_free_and_pr_safe():
    text = Path(".github/workflows/ci.yml").read_text()
    assert "pull_request:" in text
    assert "push:" in text
    assert "branches: [main]" in text
    _assert_exact_read_permissions(text)
    assert "timeout-minutes: 10" in text
    assert "python -m pytest -q" in text
    assert "schedule:" not in text
    assert "pull_request_target" not in text
    assert "ANTHROPIC_API_KEY" not in text
    assert "SNOWFLAKE_" not in text


def test_live_evals_are_manual_protected_and_upload_on_failure():
    text = Path(".github/workflows/live-evals.yml").read_text()
    assert "workflow_dispatch:" in text
    assert "environment: live-evals" in text
    _assert_exact_read_permissions(text)
    assert "timeout-minutes: 20" in text
    assert "--suite regression --ci --repeat 2" in text
    assert "--output artifacts/regression.json" in text
    assert "path: artifacts/regression.json" in text
    assert "build_snapshot" in text
    assert "SNOWFLAKE_PRIVATE_KEY_B64" in text
    assert "if: always()" in text
    assert "schedule:" not in text
    assert "capability" not in text
    assert "pull_request:" not in text
    assert "pull_request_target" not in text


def test_live_eval_failure_placeholder_precedes_external_preflight(tmp_path):
    text = Path(".github/workflows/live-evals.yml").read_text()
    initialize = text.index("      - name: Initialize failure artifact")
    key_decode = text.index("      - name: Write Snowflake private key")
    snapshot = text.index("      - name: Build local metadata snapshot")

    assert initialize < key_decode < snapshot

    run_marker = "        run: |\n"
    script_start = text.index(run_marker, initialize) + len(run_marker)
    script_end = text.index("\n      - ", script_start)
    script = textwrap.dedent(text[script_start:script_end])
    subprocess.run(["bash", "-eu", "-c", script], cwd=tmp_path, check=True)

    payload = json.loads((tmp_path / "artifacts/regression.json").read_text())
    assert payload == {
        "mode": "ci",
        "suite": "regression",
        "repeat": 0,
        "infrastructure_errors": ["workflow failed before runner output"],
        "runs": [],
    }


@pytest.mark.parametrize(
    "text",
    [
        "permissions:\n  contents: read\n  issues: write\n\njobs:\n",
        (
            "permissions:\n  contents: read\n\njobs:\n  test:\n"
            "    permissions:\n      checks: write\n"
        ),
    ],
)
def test_permissions_contract_rejects_added_write_access(text):
    with pytest.raises(AssertionError):
        _assert_exact_read_permissions(text)
