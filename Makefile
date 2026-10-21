.PHONY: test eval docs manual deploy deploy-status

test:
	pytest -q

# Live-call harness: real Anthropic + OpenAI + Snowflake. Needs .env.
# --repeat N runs the set N times: with a live model one run is a sample, not
# a measurement, and the Evals tab shows the ratio per commit.
eval:
	python -m evals.run_evals

# Manual CI equivalent: python -m evals.run_evals --suite regression --ci --repeat 2 --output artifacts/regression.json

# Rewrites the "What the ids on this page mean" table at the bottom of each
# doc, so a bare `DF-05` or `D-020` resolves without opening two other files.
# tests/test_id_reference.py fails if a doc is stale, so this is not something
# you have to remember.
docs:
	python -m scripts.build_id_reference

# Rebuild the private interview-preparation PDF. Install requirements-docs.txt
# in addition to the application dependencies before running this target.
manual:
	python scripts/generate_interview_manual.py

# One-command deploy from the laptop: push main, then have the EC2 host pull
# and rebuild. `static/` and `evals/` are COPY'd into the image, so a pull
# alone changes nothing the running container serves — the rebuild is the
# part that matters. See docs/decisions.md D-016 for why only `app` starts.
#
# Refuses to run on a dirty tree: the host deploys what is COMMITTED, so a
# dirty tree means the thing you just tested locally is not the thing that
# ships, which is the least obvious way to lose an afternoon.
deploy:
	@git diff --quiet || { echo "ERROR: uncommitted changes. Commit or stash first."; exit 1; }
	@test "$$(git branch --show-current)" = "main" || { echo "ERROR: not on main (on $$(git branch --show-current))."; exit 1; }
	git push origin main
	ssh censuschat 'cd ~/censuschat && git pull --ff-only && ./deploy.sh'
	@$(MAKE) deploy-status

# What is actually live right now, read through Caddy rather than from the
# host, so it exercises the same path a reviewer's browser takes.
#
# No credentials: Caddy asks for the login only on writes (D-034), so reads
# are public and the probe below checks that a write still needs it. The
# POST sends an empty body, so even with the gate missing it fails
# validation (422) and writes nothing. Before the gate is switched on, the
# GETs return 401 and print as unreachable; that is expected.
#
# NOTE: the sha below is the commit the EVAL RUN was recorded at, read out of
# the committed artifact — NOT the commit the container was built from. It
# only advances when evals are re-run, so it will legitimately trail main.
# There is no deployed-code version endpoint; the honest signal that a
# rebuild took is content that changed with it (row count, UI).
SITE := https://censuschat.brianmar.com

deploy-status:
	@echo "--- deployed ---"
	@curl -sf --max-time 15 $(SITE)/api/health || echo "(health unreachable)"
	@echo
	@curl -sf --max-time 15 $(SITE)/api/evals \
		| python3 -c "import sys,json; l=(json.load(sys.stdin).get('latest') or {}); print('eval artifact sha:', l.get('git_sha'), '| eval rows:', len(l.get('results',[])))" 2>/dev/null \
		|| echo "(evals unreachable)"
	@echo "--- login only for writes (D-034) ---"
	@get=$$(curl -s -o /dev/null -w '%{http_code}' --max-time 15 $(SITE)/api/health); \
	post=$$(curl -s -o /dev/null -w '%{http_code}' --max-time 15 -X POST -H 'Content-Type: application/json' -d '{}' $(SITE)/api/chat); \
	echo "GET  /api/health, no login: $$get (200 once the gate is on, 401 before)"; \
	echo "POST /api/chat {}, no login: $$post (must be 401)"; \
	if [ "$$post" = 000 ]; then echo "ERROR: site unreachable"; exit 1; fi; \
	test "$$post" = 401 || { echo "ERROR: a write answered without the login"; exit 1; }
	@echo "--- local main ---"
	@git log --oneline -1
