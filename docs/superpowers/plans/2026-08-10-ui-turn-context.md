# UI Turn Context Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Show local date-times throughout the UI and show each persisted or live assistant answer beside its question in Turn Detail and Trace Logging.

**Architecture:** Keep trace persistence unchanged. Enrich `/api/traces` at read time by pairing chronological trace questions with the canonical session transcript, then render the resulting `assistant_answer` in both technical views. Use browser-native date formatting for server-authored persisted timestamps and client-authored live-row timestamps.

**Tech Stack:** Python 3.11, FastAPI, Pydantic, SQLite session storage, vanilla JavaScript, native `Intl.DateTimeFormat`, pytest.

## Global Constraints

- Keep the three agent tools and frozen `ChatEvent` contract unchanged.
- Do not add dependencies, a frontend build step, or a second frontend file.
- Format all displayed dates in the viewer's browser locale and timezone.
- Render answers with `textContent`, preserving line breaks and never interpreting answer text as HTML.
- Treat the session store as the canonical transcript and the trace store as observability data.
- Return a null `assistant_answer` when a trace cannot be matched safely.
- Preserve all unrelated worktree changes and never stage them.

---

### Task 1: Enrich trace responses with persisted assistant answers

**Files:**
- Modify: `tests/test_app.py:350-400`
- Modify: `src/app.py:18-35,228-240`

**Interfaces:**
- Consumes: `get_traces(session_id) -> list[TurnTrace]` and `get_session(session_id) -> Session`.
- Produces: `_assistant_answers_for_traces(records: list[TurnTrace], messages: list[ChatMessage]) -> list[str | None]` and an `assistant_answer` field on every serialized `/api/traces` item.

- [ ] **Step 1: Write the failing endpoint tests**

Add tests that monkeypatch `src.app.get_traces` and `src.app.get_session` with three traces named `same question`, `missing question`, and `same question`, while the session contains two complete `same question` pairs. Assert the response answers are `first answer`, `None`, and `second answer` in that order. Add a second test whose patched `get_session` raises and assert `/api/traces` still returns HTTP 200 with a null answer.

```python
def test_traces_endpoint_pairs_repeated_answers_without_shifting(monkeypatch):
    started = datetime(2026, 8, 7, 21, 57, tzinfo=timezone.utc)
    records = [
        TurnTrace(session_id="s", user_message=q, started_at=started, total_ms=1)
        for q in ["same question", "missing question", "same question"]
    ]
    messages = [
        ChatMessage(role="user", content="same question"),
        ChatMessage(role="assistant", content="first answer"),
        ChatMessage(role="user", content="same question"),
        ChatMessage(role="assistant", content="second answer"),
    ]
    monkeypatch.setattr("src.app.get_traces", lambda _sid: records)
    monkeypatch.setattr(
        "src.app.get_session", lambda _sid: Session(session_id="s", messages=messages)
    )

    body = client.get("/api/traces", params={"session_id": "s"}).json()

    assert [t["assistant_answer"] for t in body["traces"]] == [
        "first answer",
        None,
        "second answer",
    ]
```

- [ ] **Step 2: Run the focused tests and verify RED**

Run: `.venv/bin/pytest tests/test_app.py -k 'traces_endpoint_pairs or traces_endpoint_survives_broken_session' -q`

Expected: FAIL because `/api/traces` does not return `assistant_answer` and `src.app.get_session` is not imported.

- [ ] **Step 3: Implement chronological, question-checked pairing**

Import `ChatMessage`, `Session`, `get_session`, and `TurnTrace`. Implement `_assistant_answers_for_traces` with a single forward cursor. For each trace, find the next user message whose content exactly matches `trace.user_message`, use the first assistant message before the following user message, and advance the cursor only after a match. If no match or answer exists, append `None` without consuming a later user turn.

Update `/api/traces` to fetch traces and session history on worker threads. Catch session-read failures, log them, and use an empty message list. Serialize each trace as before, then add the paired `assistant_answer` field. Do not modify `TurnTrace` or the trace database schema.

- [ ] **Step 4: Run the focused tests and verify GREEN**

Run: `.venv/bin/pytest tests/test_app.py -k 'traces_endpoint' -q`

Expected: all trace endpoint tests PASS.

- [ ] **Step 5: Commit the backend slice**

```bash
git add src/app.py tests/test_app.py
git commit -m "feat: include answers in trace responses"
```

---

### Task 2: Render dates, timestamps, and answers in the frontend

**Files:**
- Create: `tests/test_static.py`
- Modify: `static/index.html:240-345,1035-1135,1214-1345,1392-1558`

**Interfaces:**
- Consumes: `/api/trace-sessions.last_at`, `/api/traces[].started_at`, and `/api/traces[].assistant_answer` from Task 1.
- Produces: `formatDateTime(value) -> string`, `appendTimestamp(parent, value) -> HTMLElement | null`, and `appendAnswer(parent, answer) -> HTMLElement` in `static/index.html`.

- [ ] **Step 1: Write the failing static wiring tests**

Create `tests/test_static.py` that reads `static/index.html` and asserts the shared helpers and every required caller are present.

```python
from pathlib import Path


HTML = Path("static/index.html").read_text()


def test_timestamp_formatter_is_wired_to_all_views():
    assert "function formatDateTime(value)" in HTML
    assert "formatDateTime(s.last_at)" in HTML
    assert HTML.count("formatDateTime(trace.started_at)") >= 2
    assert 'addRow("user", turnStartedAt)' in HTML
    assert 'addRow("assistant", turnStartedAt)' in HTML


def test_answers_are_wired_to_stored_and_live_turn_views():
    assert "appendAnswer(wrap, trace.assistant_answer)" in HTML
    assert "appendAnswer(details, trace.assistant_answer)" in HTML
    assert "flowSteps._answerEl.textContent = assistantText" in HTML
    assert 'text.textContent = answer || "Answer not recorded"' in HTML
```

- [ ] **Step 2: Run the static tests and verify RED**

Run: `.venv/bin/pytest tests/test_static.py -q`

Expected: both tests FAIL because the helpers and rendering calls do not exist.

- [ ] **Step 3: Add the minimal styles and shared DOM helpers**

Add muted `.timestamp` and `.message-time` styles. Add `.turn-answer` with a small label and `.turn-answer-text` with `white-space: pre-wrap` and `overflow-wrap: anywhere`.

Implement `formatDateTime` using `new Intl.DateTimeFormat(undefined, {year: "numeric", month: "short", day: "numeric", hour: "numeric", minute: "2-digit", timeZoneName: "short"})`. Return an empty string for missing values or invalid dates. `appendTimestamp` omits its element when formatting returns empty. `appendAnswer` creates its label and answer text with DOM methods and `textContent` only.

- [ ] **Step 4: Wire persisted session, Turn Detail, and Trace Logging data**

In `labelForSession`, include `formatDateTime(s.last_at)` before the turn count and omit the separator when empty. In `renderStoredTurn`, append the formatted `trace.started_at` and `appendAnswer(wrap, trace.assistant_answer)` directly below the question. In `renderTrace`, replace `toLocaleTimeString()` with `formatDateTime(trace.started_at)` and append the answer block before the spans table.

- [ ] **Step 5: Wire live Chat and Turn Detail data**

Change `addRow(role)` to `addRow(role, startedAt)` and append a message timestamp outside the bubble. At the start of `sendMessage`, capture one `turnStartedAt = new Date().toISOString()` and pass it to the user row, assistant row, and `startFlowTurn(message, turnStartedAt)`.

Have `startFlowTurn` append its timestamp and an initially empty answer block, then attach the returned text element as `steps._answerEl`. On every token event, assign the full `assistantText` to both the chat bubble and `flowSteps._answerEl.textContent`. On error, empty-DONE, and unexpected-stream termination paths, assign the same visible fallback text to the Turn Detail answer element.

- [ ] **Step 6: Run the static and endpoint tests and verify GREEN**

Run: `.venv/bin/pytest tests/test_static.py tests/test_app.py -q`

Expected: all tests PASS.

- [ ] **Step 7: Commit the frontend slice**

```bash
git add static/index.html tests/test_static.py
git commit -m "feat: show timestamps and answers in turn history"
```

---

### Task 3: Record the data-flow decision and verify the application

**Files:**
- Modify: `docs/decisions.md`
- Modify: `CHANGELOG.md`
- Modify: `docs/superpowers/specs/2026-08-10-ui-timestamps-design.md`
- Create: `docs/superpowers/plans/2026-08-10-ui-turn-context.md`

**Interfaces:**
- Consumes: the completed backend and frontend behavior from Tasks 1 and 2.
- Produces: decision D-024 documenting the read-time transcript join and reviewer-facing change history.

- [ ] **Step 1: Add D-024 and changelog entries**

Document that answers remain canonical in `sessions.sqlite3`, `/api/traces` enriches trace rows by chronological exact-question matching, unmatched rows remain null, and duplicating answer text into `traces.sqlite3` was rejected. Add concise `Added` entries for local date-times and answer display.

- [ ] **Step 2: Regenerate reference blocks**

Run: `make docs`

Expected: command succeeds. Inspect any generated diff and keep only expected reference updates.

- [ ] **Step 3: Run the full offline suite**

Run: `make test`

Expected: all tests PASS with no warnings or errors.

- [ ] **Step 4: Perform browser verification**

Start the local app with `uvicorn src.app:app --reload`, open the UI, and verify an earlier session plus one new turn. Confirm both session pickers, Turn Detail, Trace Logging, user Chat rows, and assistant Chat rows display local date-times. Confirm both technical views show the exact full persisted answer and no `Invalid Date` text appears.

- [ ] **Step 5: Commit documentation and plan**

```bash
git add CHANGELOG.md docs/decisions.md docs/superpowers/specs/2026-08-10-ui-timestamps-design.md docs/superpowers/plans/2026-08-10-ui-turn-context.md
git commit -m "docs: record turn context display decision"
```
