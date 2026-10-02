from pathlib import Path
import json
import re
import subprocess

import pytest


def _html() -> str:
    return Path("static/index.html").read_text()


def test_exact_reviewer_tab_order():
    html = _html()
    labels = ["Chat", "Evidence", "Evals", "Design Trade-Offs", "How It Works"]
    positions = [html.index(f">{label}</button>") for label in labels]
    assert positions == sorted(positions)
    assert html.count('data-tab="') == 5


def test_tradeoffs_tab_is_wired_and_complete():
    html = _html()

    assert 'aria-controls="panel-tradeoffs"' in html
    assert 'tradeoffs: document.getElementById("panel-tradeoffs")' in html
    assert html.count('class="tradeoff-card"') == 14
    assert 'button.setAttribute("aria-selected", String(selected))' in html
    assert 'panels[key].hidden = !selected' in html
    assert 'event.key === "ArrowRight"' in html
    assert 'event.key === "ArrowLeft"' in html
    assert 'event.key === "Home"' in html
    assert 'event.key === "End"' in html


def test_tab_scroll_container_does_not_expose_vertical_scrollbar():
    html = _html()
    tabs_rule = re.search(r"\.tabs\s*\{([^}]*)\}", html)

    assert tabs_rule is not None
    assert "overflow-x: auto" in tabs_rule.group(1)
    assert "overflow-y: hidden" in tabs_rule.group(1)


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


def test_new_chat_control_starts_a_fresh_persisted_session():
    html = _html()
    assert 'id="new-chat"' in html
    assert "let sessionId" in html
    assert 'localStorage.setItem("censuschat_session_id", sessionId)' in html
    assert 'logEl.textContent = ""' in html
    assert "newChatEl.disabled = busy" in html
    assert 'newChatEl.addEventListener("click", startNewChat)' in html


def test_contextual_questions_fill_input_without_submitting_and_protect_drafts():
    html = _html()

    assert "offers.slice(0, 3).forEach" in html
    assert "button.textContent = offer.question" in html
    assert "inputEl.value = offer.question" in html
    assert "inputEl.focus()" in html
    assert "window.confirm" in html
    assert "sendMessage(offer" not in html
    assert "suggestion_id" not in html


def test_contextual_questions_clear_and_reject_stale_terminal_events():
    html = _html()

    assert "const requestTurn = ++activeChatTurn" in html
    assert "if (requestTurn === activeChatTurn && completedWithAnswer)" in html
    assert "activeChatTurn += 1" in html
    assert "if (ended) continue" in html
    assert "clearFollowUps()" in html


def test_contextual_question_buttons_wrap_and_render_plain_text():
    html = _html()

    follow_up_rule = re.search(r"\.follow-ups\s*\{([^}]*)\}", html)
    assert follow_up_rule is not None
    assert "display: flex" in follow_up_rule.group(1)
    assert "flex-wrap: wrap" in follow_up_rule.group(1)
    assert "button.innerHTML" not in html


def test_old_evidence_response_cannot_repopulate_a_new_chat():
    html = _html()
    assert "const requestedSessionId = historySessionId" in html
    assert html.count("if (requestedSessionId !== historySessionId) return;") == 3


def test_evidence_defaults_to_curated_trace_with_optional_raw_json():
    html = _html()
    assert 'id="evidence-content"' in html
    assert "Raw trace JSON" in html
    assert "<details" in html
    assert "trace.final_answer" in html
    assert "trace.terminal_status" in html


def test_evidence_explains_model_calls_from_observable_metadata():
    html = _html()

    assert "function describeModelCall(meta)" in html
    assert 'spanStepName(span)' in html
    assert '"Census agent decision"' in html
    assert '"Census agent response"' in html
    assert 'setStepDetail(item, describeModelCall(meta))' in html
    assert '["recovery attempts", meta.recovery_attempts]' in html


def _progress_steps(events):
    html = _html()
    model = html.split("// BEGIN PROGRESS MODEL", 1)[1].split("\n", 1)[1]
    model = model.split("// END PROGRESS MODEL", 1)[0]
    runner = model + """
      const events = JSON.parse(process.argv[1]);
      let state = { steps: [{ label: "Census data check", state: "running" }] };
      let answer = "";
      for (const event of events) {
        state = advanceProgress(state, event, Boolean(answer.trim()));
        if (event.type === "token") answer += event.data.text;
      }
      process.stdout.write(JSON.stringify(state.steps));
    """
    result = subprocess.run(
        ["node", "-e", runner, json.dumps(events)],
        check=True, capture_output=True, text=True,
    )
    return [(step["label"], step["state"]) for step in json.loads(result.stdout)]


@pytest.mark.parametrize(
    ("events", "expected"),
    [
        (
            [{"type": "token", "data": {"text": "Data unavailable."}}, {"type": "done"}],
            [("Census data check", "failed"), ("Response stream", "done")],
        ),
        (
            [{"type": "status", "data": {"message": "Request check"}},
             {"type": "token", "data": {"text": "I can only help with census data."}},
             {"type": "done"}],
            [("Census data check", "done"), ("Request check", "done"),
             ("Response stream", "done")],
        ),
        (
            [{"type": "status", "data": {"message": "Request check"}},
             {"type": "status", "data": {"message": "Census agent decision"}},
             {"type": "tool_start", "data": {"tool": "search_census_variables"}},
             {"type": "tool_end", "data": {"tool": "search_census_variables", "ok": True}},
             {"type": "tool_start", "data": {"tool": "resolve_geography"}},
             {"type": "tool_end", "data": {"tool": "resolve_geography", "ok": True}},
             {"type": "status", "data": {"message": "Census agent decision"}},
             {"type": "token", "data": {"text": "Result."}}, {"type": "done"}],
            [("Census data check", "done"), ("Request check", "done"),
             ("Census agent decision", "done"), ("Variable finder", "done"),
             ("Geography resolver", "done"), ("Census agent decision", "done"),
             ("Response stream", "done")],
        ),
        (
            [{"type": "status", "data": {"message": "Request check"}},
             {"type": "status", "data": {"message": "Census agent decision"}},
             {"type": "tool_start", "data": {"tool": "run_census_sql"}},
             {"type": "tool_end", "data": {"tool": "run_census_sql", "ok": True,
                                           "summary": {"row_count": 0}}},
             {"type": "status", "data": {"message": "Census agent recovery"}},
             {"type": "token", "data": {"text": "No rows matched."}}, {"type": "done"}],
            [("Census data check", "done"), ("Request check", "done"),
             ("Census agent decision", "done"), ("Census query", "empty"),
             ("Census agent recovery", "done"), ("Response stream", "done")],
        ),
        (
            [{"type": "status", "data": {"message": "Request check"}},
             {"type": "error"}],
            [("Census data check", "done"), ("Request check", "failed")],
        ),
    ],
)
def test_stream_progress_keeps_only_real_steps_and_terminal_states(events, expected):
    assert _progress_steps(events) == expected


def test_clean_zero_tool_done_turn_is_not_rendered_as_failed():
    html = _html()

    assert 'toolStepsSeen ? "ok" : "fail"' not in html
    assert '"Completed without tool calls"' in html


def test_legacy_blank_done_trace_is_rendered_as_an_error():
    html = _html()

    assert 'trace.terminal_status === "done" && !trace.final_answer.trim()' in html
    assert '? "error"' in html


def test_live_blank_done_event_is_rendered_as_failed():
    html = _html()

    assert "const completedWithAnswer = Boolean(assistantText.trim())" in html
    assert 'completedWithAnswer ? "ok" : "fail"' in html
    assert 'completedWithAnswer ? "done" : "error"' in html


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


def test_legacy_eval_artifacts_are_not_labeled_as_current_regression_proof():
    html = _html()

    assert "run.legacy" in html
    assert "Legacy committed benchmark" in html
    assert "Derived regression grouping" in html
    assert '"legacy " + checkOutcome' in html


def test_legacy_history_summary_is_appended_after_latest_benchmark():
    html = _html()

    assert "function renderLegacyHistory(history, latest)" in html
    assert "Legacy historical runs" in html
    assert 'filter(function (run) { return run.legacy; })' in html
    assert 'outcome !== "pass"' in html
    assert 'entries.length > 0 && notPassing.length === 0' in html
    assert "Benchmark shown above" in html
    assert "renderEvalRun(data.latest) + renderLegacyHistory(data.history, data.latest)" in html
