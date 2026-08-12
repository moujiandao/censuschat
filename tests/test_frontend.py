from pathlib import Path


def _html() -> str:
    return Path("static/index.html").read_text()


def test_exact_reviewer_tab_order():
    html = _html()
    labels = ["Chat", "How It Works", "Evidence", "Evals"]
    positions = [html.index(f">{label}</button>") for label in labels]
    assert positions == sorted(positions)
    assert html.count('data-tab="') == 4


def test_legacy_top_level_surfaces_are_removed():
    html = _html()
    assert ">Turn Detail</button>" not in html
    assert ">Trace Logging</button>" not in html
    assert ">Data Source</button>" not in html


def test_how_it_works_names_the_three_protection_layers():
    html = _html()
    assert "Code protections" in html
    assert "Model instructions" in html
    assert "Evaluation checks" in html


def test_chat_has_four_example_questions():
    html = _html()
    assert "Example questions" in html
    assert html.count('class="example-question"') == 4


def test_evidence_defaults_to_curated_trace_with_optional_raw_json():
    html = _html()
    assert 'id="evidence-content"' in html
    assert "Raw trace JSON" in html
    assert "<details" in html


def test_empty_and_failed_technical_views_have_explicit_copy():
    html = _html()
    assert "No turns recorded for this session yet." in html
    assert "Couldn't load evidence." in html
    assert "No eval runs recorded yet." in html
    assert "Couldn't load eval results." in html


def test_normalization_copy_matches_the_implemented_result_seam():
    html = _html()
    assert "SQL NULL or a suppression code" not in html
    assert "No numeric suppression codes were observed or transformed." in html
    assert "Derived and aggregate projections are not normalized." in html


def test_historical_eval_values_are_whitelisted_before_class_interpolation():
    html = _html()
    assert "function suiteFor(row)" in html
    assert "function outcomeFor(row)" in html
    assert "suiteOrder.indexOf(row.suite)" in html
    assert "outcomeOrder.indexOf(row.outcome)" in html
    assert '"badge " + escapeHtml(' not in html
