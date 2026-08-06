.PHONY: test eval deploy deploy-status

test:
	pytest -q

# Live-call harness: real Anthropic + real Snowflake. Needs .env.
eval:
	python -m evals.run_evals

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
deploy-status:
	@echo "--- deployed ---"
	@curl -s -u snowflake:census --max-time 15 https://censuschat.brianmar.com/api/health || echo "(health unreachable)"
	@echo
	@curl -s -u snowflake:census --max-time 15 https://censuschat.brianmar.com/api/evals \
		| python3 -c "import sys,json; l=(json.load(sys.stdin).get('latest') or {}); print('serving git_sha:', l.get('git_sha'), '| eval rows:', len(l.get('results',[])))" \
		|| echo "(evals unreachable)"
	@echo "--- local main ---"
	@git log --oneline -1
