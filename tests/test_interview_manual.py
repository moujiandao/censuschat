from pathlib import Path

import pytest
from pypdf import PdfReader

from scripts import generate_interview_manual as manual


def test_build_manual_contract(tmp_path: Path) -> None:
    output = tmp_path / "manual.pdf"

    manual.build_manual(output)

    reader = PdfReader(output)
    metadata = reader.metadata
    text = "\n".join(page.extract_text() or "" for page in reader.pages)
    normalized_text = " ".join(text.split())

    assert 12 <= len(reader.pages) <= 16
    assert metadata.title == "CensusChat Interview Manual"
    assert metadata.author == "Brian Mar - private interview preparation"
    assert metadata.subject == "Snowflake Applied AI candidate homework interview playbook"
    assert all(float(page.mediabox.width) == 612 for page in reader.pages)
    assert all(float(page.mediabox.height) == 792 for page in reader.pages)
    assert "search_census_variables" in text
    assert "resolve_geography" in text
    assert "run_census_sql" in text
    assert "THREE AGENT TOOLS" in text
    assert "EVIDENCE" in text.upper()
    assert "TRACE STORE" in text.upper()
    assert "persist spans, answer, status" in text
    assert "Why is the 50-second watchdog soft?" in text
    assert "INTERVIEW CHEAT SHEET" in text.upper()
    assert "1. Start with the smallest unit" in normalized_text
    assert "Harris County population = sum of its block-group population counts" in normalized_text
    assert "Two block groups report median household incomes of $55k and $95k" in normalized_text
    assert "does not make the county median $75k" in normalized_text
    assert "QUICK RULES" in text
    assert "The aggregation rules to explain" not in text


def test_build_manual_requires_ui_screenshot(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    missing = tmp_path / "missing.png"
    monkeypatch.setattr(manual, "UI_SCREENSHOT", missing)

    with pytest.raises(FileNotFoundError, match="UI screenshot not found"):
        manual.build_manual(tmp_path / "manual.pdf")
