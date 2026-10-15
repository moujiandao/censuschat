"""The How It Works flow diagram must describe code that exists.

Ported from the System flow check in the customs packet review project: every
`file.py::name` on the diagram, every name in the file table, and every name in
the numbered steps is resolved against `src/`, so a rename fails the suite
before the page goes stale.
"""

import ast
import re
from pathlib import Path

from src import agent, guardrail, model_config
from src.contracts import (
    MAX_RECOVERY_RETRIES,
    SQL_ROW_LIMIT,
    SQL_STATEMENT_TIMEOUT_S,
    TURN_DEADLINE_S,
)

SRC = Path("src")
REFERENCE = re.compile(r"(\w+)\.py::([\w.]+)")
STEPS = [
    ("app", "chat"),
    ("sessions", "get_session"),
    ("guardrail", "classify_input"),
    ("agent", "agent_turn"),
    ("tools", "search_census_variables"),
    ("tools", "resolve_geography"),
    ("tools", "run_census_sql"),
    ("snapshot", "build_snapshot"),
    ("sqlgate", "validate_sql"),
    ("snowflake_conn", "connect"),
    ("follow_ups", "questions"),
    ("tracing", "record_turn_trace"),
]
FILES = [
    "app.py",
    "sessions.py",
    "guardrail.py",
    "agent.py",
    "tools.py",
    "snapshot.py",
    "sqlgate.py",
    "health.py",
    "snowflake_conn.py",
    "follow_ups.py",
    "tracing.py",
    "model_config.py",
]


def _html() -> str:
    return Path("static/index.html").read_text()


def _panel() -> str:
    html = _html()
    start = html.index('id="panel-how"')
    # <wbr> only marks where a long reference may wrap.
    return html[start : html.index("</section>", start)].replace("<wbr>", "")


def _between(text: str, start: str, end: str) -> str:
    begin = text.index(start)
    return text[begin : text.index(end, begin)]


def defined_in(module: str, name: str) -> bool:
    """True only when the module itself defines the name; an import does not count."""
    path = SRC / f"{module}.py"
    if not path.is_file():
        return False
    for node in ast.parse(path.read_text()).body:
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef):
            if node.name == name:
                return True
        elif isinstance(node, ast.Assign):
            if any(isinstance(t, ast.Name) and t.id == name for t in node.targets):
                return True
        elif isinstance(node, ast.AnnAssign):
            if isinstance(node.target, ast.Name) and node.target.id == name:
                return True
    return False


def table_rows(panel: str) -> list[tuple[str, list[str]]]:
    table = _between(panel, '<table class="how-files">', "</table>")
    rows = re.findall(r"<tr><td><code>(\w+)\.py</code></td>(.*?)</tr>", table, re.S)
    return [(module, re.findall(r"<code>([\w.]+)</code>", cells)) for module, cells in rows]


def test_resolver_rejects_names_the_module_does_not_define():
    assert defined_in("app", "chat")
    assert defined_in("model_config", "AGENT_MODEL")
    assert defined_in("contracts", "TURN_DEADLINE_S")  # an annotated assignment
    assert not defined_in("app", "no_such_route")
    assert not defined_in("no_such_module", "chat")
    # Imported names are usable in the module but live somewhere else.
    assert not defined_in("agent", "classify_input")
    assert not defined_in("tools", "validate_sql")


def test_every_step_reference_points_at_real_code():
    diagram = _between(_panel(), '<div class="flow">', '<p class="flow-note">')
    references = REFERENCE.findall(diagram)
    assert references == STEPS
    for module, name in references:
        assert defined_in(module, name), f"{module}.py::{name} is shown but not defined there"


def test_file_table_names_real_files_and_functions():
    rows = table_rows(_panel())
    assert [f"{module}.py" for module, _ in rows] == FILES
    for module, names in rows:
        assert (SRC / f"{module}.py").is_file()
        assert names, f"{module}.py row names nothing from the file"
        for name in names:
            assert defined_in(module, name), f"{module}.py does not define {name}"


def _turn_steps(panel: str) -> list[tuple[str, str]]:
    steps = _between(panel, '<ol class="how-steps" role="list">', "</ol>")
    return re.findall(r"<li><h3>(.*?)</h3>\s*<p>(.*?)</p></li>", steps, re.S)


def test_numbered_steps_follow_the_file_table_and_read_as_prose():
    panel = _panel()
    # The steps sit directly under the file table, before the protection cards.
    assert panel.index('<table class="how-files">') < panel.index('<ol class="how-steps" role="list">')
    assert panel.index('<ol class="how-steps" role="list">') < panel.index("Code protections")
    steps = _turn_steps(panel)
    assert len(steps) == 13
    assert _between(panel, '<ol class="how-steps" role="list">', "</ol>").count("<li>") == len(steps)
    for title, prose in steps:
        # A paragraph of explanation per step, not a one-line caption.
        words = len(re.sub(r"<[^>]+>", "", prose).split())
        assert words >= 50, f"step '{title}' has only {words} words"


def test_connection_steps_only_use_checked_names():
    panel = _panel()
    steps = _between(panel, '<ol class="how-steps" role="list">', "</ol>")
    names = re.findall(r"<code>([\w.]+)</code>", steps)
    checked = {name for _, name in STEPS} | {n for _, row in table_rows(panel) for n in row}
    assert len(names) >= 12
    for name in names:
        if name.endswith(".py"):
            assert (SRC / name).is_file(), f"{name} is not a file in src/"
        else:
            assert name in checked, f"{name} is mentioned but not checked against the code"


def test_diagram_opens_the_tab_and_replaces_the_text_strip():
    html = _html()
    panel = _panel()
    # D-027: the request path has one depiction, and it lives in How It Works.
    assert 'class="how-flow"' not in html
    assert "Question → Guardrail" not in html
    assert panel.index('class="flow-frame"') < panel.index("Code protections")
    assert panel.index("What each file does") < panel.index("How everything connects")


def test_models_are_named_by_role_not_by_id():
    # Rule 14: a model id is named in one place, src/model_config.py.
    html = _html()
    assert model_config.AGENT_MODEL not in html
    assert model_config.CLASSIFIER_MODEL not in html
    panel = _panel()
    assert "Agent model" in panel
    assert "Guardrail model" in panel


def test_limits_on_the_page_match_the_contract():
    # The words are written by hand. Update them when the limits change.
    panel = _panel()
    assert f"{int(TURN_DEADLINE_S)} second watchdog" in panel
    assert MAX_RECOVERY_RETRIES == 2
    assert "two failed or empty queries" in panel
    assert f"{SQL_STATEMENT_TIMEOUT_S} second statement timeout" in panel
    assert f"cap of {SQL_ROW_LIMIT} rows" in panel
    assert agent._MAX_TOOL_LOOP_ITERATIONS == 8 and "at most eight rounds" in panel
    assert guardrail._TIMEOUT_S == 1.5 and "1.5 seconds" in panel


def test_page_says_how_its_references_are_checked():
    assert "tests/test_system_flow.py" in _between(_panel(), '<p class="how-inspected">', "</p>")
