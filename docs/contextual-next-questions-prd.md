# Contextual Next Questions

Date: 2026-09-16
Status: Approved by Brian for implementation on 2026-09-16. Deployment remains separate.

## Problem Statement

CensusChat visitors may not know what to ask after an answer. A single suggested next step limits exploration, while generic suggestions can send visitors toward unsupported data or calculations.

## Solution

Show up to three short, contextual question buttons beneath the latest completed answer. Offer distinct, supported directions when useful; show fewer or none when appropriate. Selecting a button fills and focuses the existing chat input without sending it, so visitors can edit before submitting through the normal chat workflow.

Example after comparing population and households in Alameda and Contra Costa counties:

- “Compare renter share in these counties.”
- “Compare these counties with another county.”
- “Compare their age distributions.”

These illustrate the desired experience. Enable each question family only after verifying its data requirements and exercising it through the real app. An unspecified additional county should trigger the app's existing clarification behavior.

## User Stories

1. As a visitor, I want several relevant next questions so I can choose a useful direction.
2. As a visitor, I want suggestions to retain my places and comparison context so I need not repeat myself.
3. As a visitor, I want to edit a suggestion before submitting it so I control the request.
4. As a visitor, I want suggestions to respect dataset limits so I avoid predictable dead ends.
5. As a visitor using a keyboard or phone, I want accessible buttons that remain easy to read and select.

## Implementation Decisions

Proposed MVP:

- Use a small catalogue of tested question templates, selected by deterministic eligibility rules using resolved geography, available discovery results, and conversation context. Broader model-generated suggestions are deferred. Tradeoff: less variety in exchange for predictable coverage and no extra model call.
- Give each template explicit prerequisites: supported geography, metric availability, and a valid operation. If prerequisites cannot be established, omit it. Do not infer support merely from plausible model prose.
- Prefer distinct directions: another supported metric, another geography, or a supported breakdown. Remove duplicates and questions already answered without a meaningful change in scope.
- Display zero to three suggestions. Never fill an arbitrary quota. Suggestions contain questions, not new numeric claims or promised conclusions.
- Suppress general exploration while geography or intent remains ambiguous. Keep the existing clarification question prominent; building selectable clarification choices is outside this MVP.
- After a dataset-coverage refusal, offer only a verified answerable alternative. Show none after off-topic refusals, outages, failed turns, or exhausted recovery.
- Render suggestions only for the latest completed turn. Remove them when another request starts or a new conversation begins; reject late updates from an older turn or session. Do not overwrite nonempty user input without an explicit replacement choice.
- Keep generation and filtering on the server, with plain-text buttons in the existing frontend. Pass suggestions as optional metadata on the existing terminal response event, separate from answer prose. Do not parse suggestions from streamed Markdown.
- The metadata addition is a proposed public-contract change. Obtain approval and record any required invariant deviation before implementing it. Preserve existing event types, the three agent tools, SQL validation, and streaming behavior.
- Add no separate LLM request, speculative Snowflake query, new dependency, database table, or frontend build step. Missing or invalid suggestion metadata must leave the answer usable.
- Do not inject Census variable IDs or labels into prompts. Runtime discovery remains the source of variable information.

## Testing Decisions

Test observable selection and interaction behavior, not exact internal implementation. Use existing deterministic test conventions for eligibility and existing streaming tests for compatibility. Validate model behavior with focused live scenarios.

Acceptance criteria:

1. A supported comparison with three eligible directions displays three distinct questions; one eligible direction displays one; no eligible direction displays none.
2. Questions preserve the correct geography and context across follow-ups.
3. Forecasts, unsupported geographic levels, and invalid median aggregation are never offered as supported next steps.
4. Ambiguous requests and failed turns have no unrelated exploration buttons.
5. Clicking a question populates the input without a network request. Editing and submitting uses the existing chat path. Existing draft text is protected.
6. Starting another turn or conversation clears stale suggestions. Duplicate or delayed terminal events cannot restore them.
7. Keyboard selection works, buttons wrap on narrow screens, and labels render as text rather than executable markup.
8. Missing, malformed, or empty suggestion metadata does not break streaming, completion, or the answer.
9. Each enabled question family is exercised through a focused live scenario, including a follow-up and an unsupported case. Retain and triage failures; do not claim universal answerability from these checks.

Success means visitors can choose among useful next steps without extra model requests or a regression in existing chat behavior.

## Out of Scope

Charts, richer comparison layouts, new datasets, new geography support, automatic submission, saved suggestion history, personal recommendations, a separate recommendation agent, analytics dashboards, and unrelated chat fixes.

## Further Notes

Handoff checkpoint: baseline inspection found the checked renter-share action already merged on `main`. The approved implementation replaces that direct action with editable contextual questions rather than creating a second suggestion system.

Next phases: complete offline verification and code review, then run focused live scenarios only with explicit authorization. Deployment is not authorized by this PRD.
