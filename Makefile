.PHONY: test eval

test:
	pytest -q

# Live-call harness: real Anthropic + real Snowflake. Needs .env.
eval:
	python -m evals.run_evals
