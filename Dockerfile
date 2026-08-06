FROM python:3.13-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY src/ src/
# Both are served at runtime, so both must be in the image: src/app.py's
# GET / returns static/index.html (without this the whole web UI 500s in
# the container while working fine locally), and GET /api/evals reads
# evals/results/latest.json.
COPY static/ static/
COPY evals/ evals/

# Snapshot + session-store SQLite files land here (issues #2, #8), mounted
# as a named volume in docker-compose.yml so they persist across restarts.
RUN mkdir -p /app/data

RUN useradd --system --create-home --shell /usr/sbin/nologin appuser \
    && chown -R appuser:appuser /app
USER appuser

EXPOSE 8000

CMD ["uvicorn", "src.app:app", "--host", "0.0.0.0", "--port", "8000"]
