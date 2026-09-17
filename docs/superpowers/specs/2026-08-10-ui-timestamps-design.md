# UI Turn Context Design

## Goal

Make persisted sessions and turns easy to identify by date, make live chat
messages visibly time-ordered, and show each assistant answer beside the
question that produced it in both technical views.

## Scope

All displayed dates and times use the viewer's browser locale and timezone. A
typical US display is `Aug 7, 2026, 2:57 PM PDT`.

The frontend will display timestamps in four places:

1. Both history session pickers show the session's last activity time from
   `last_at`.
2. Stored Turn Detail cards show the turn's `started_at` beneath the title.
3. Trace Logging summaries show the full `started_at` date and time instead of
   time alone.
4. Live user and assistant chat rows show the browser time at which the row is
   created. Live Turn Detail cards use the same client-side timestamp.

Missing or invalid timestamp values render as no timestamp rather than an
invalid-date label or an exception.

Both Turn Detail and Trace Logging display the full assistant answer with its
question. This applies to earlier persisted sessions and new live turns. A turn
with no matching persisted assistant message displays `Answer not recorded`.

## Design

Add one small formatting helper to `static/index.html`. It accepts a date-like
value, rejects missing or invalid values, and formats valid values with the
browser's native `Intl.DateTimeFormat`. No formatting dependency or custom
timezone logic is needed.

Persisted views continue to use server-authored ISO timestamps returned by the
existing endpoints:

- `/api/trace-sessions` provides `last_at`.
- `/api/traces` provides each turn's `started_at`.

The trace database remains observability-only and is not migrated. The
`/api/traces` presentation endpoint reads the same session's persisted messages
and enriches each serialized trace with `assistant_answer`. It pairs turns in
chronological order, requiring the stored user message to match the trace's
`user_message` before consuming the following assistant message. An unmatched
trace receives a null answer rather than a guessed or shifted answer.

Live chat and Turn Detail rows use one ISO timestamp captured by the browser at
row creation. This is presentation metadata only and is not written back to the
server.

Live Turn Detail creates an answer area when the turn begins and updates it from
the same token stream used by the Chat bubble. Stored Turn Detail and Trace
Logging render `assistant_answer` returned by `/api/traces`. Answers render as
plain text, preserving line breaks and never using `innerHTML`.

Timestamp text uses the existing muted visual style or a narrowly scoped muted
class. It must remain readable on mobile and must not displace message content.

## Error Handling

The formatter returns an empty string for absent or unparseable input. Callers
omit the timestamp element or separator when the formatted value is empty.
Existing fetch-failure behavior remains unchanged.

If session-history retrieval fails, `/api/traces` still returns the trace data
with null answers. If one persisted turn is incomplete or cannot be matched,
only that trace lacks an answer; later matching does not consume the wrong
message.

## Verification

Add a backend test proving `/api/traces` pairs answers with repeated and
incomplete turns without shifting them. Add a focused static-UI regression test
that verifies the shared formatter, all four timestamp paths, and both answer
renderers remain wired. Run the focused tests first to observe the expected
failures, then make the minimal implementation changes and run the full offline
test suite.

Manually verify:

- An earlier session shows a date and time in both history selectors.
- Stored Turn Detail and Trace Logging entries show matching local date-times.
- New user and assistant chat rows show timestamps.
- A missing `last_at` on a new empty session produces no invalid-date text.
- Earlier Turn Detail and Trace Logging entries show the complete persisted
  answer beside the corresponding question.
- A live Turn Detail answer updates as the Chat answer streams.

## Out of Scope

- Hydrating the Chat tab from persisted history after reload.
- Adding timestamps to SSE or other backend contracts.
- Duplicating assistant answers into the trace database.
- Letting users select a timezone or date format.
- Changing trace retention, session storage, or authentication.
