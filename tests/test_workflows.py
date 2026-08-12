from pathlib import Path


def test_offline_ci_is_credential_free_and_pr_safe():
    text = Path(".github/workflows/ci.yml").read_text()
    assert "pull_request:" in text
    assert "push:" in text
    assert "branches: [main]" in text
    assert "contents: read" in text
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
    assert "contents: read" in text
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
