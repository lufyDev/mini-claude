# Runs on anything that takes a Dockerfile: Cloud Run, Railway, Fly, Render.
FROM python:3.13-slim

# The agent gets a shell, so it must not be root and must not own the source.
RUN useradd --create-home --uid 10001 agent

WORKDIR /app

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# The source stays owned by root, so the agent's shell can read it (it is public
# anyway) but cannot edit it.
# /data is where a mounted volume goes. Created and owned here so the image
# works with no volume attached too -- the app probes it at startup and falls
# back to an in-image directory if a mounted volume turns out to be root-owned.
RUN mkdir -p /data /app/.miniclaude/sessions \
 && chown -R agent:agent /data /app/.miniclaude \
 && rm -f /app/.env \
 && rm -rf /app/docs

# No USER here on purpose: entrypoint.py starts as root only long enough to
# chown the mounted volume, then drops to `agent` before exec'ing uvicorn.
# The app never runs as root.

# Public deployments are locked down by default; override to loosen, not tighten.
ENV DEMO_MODE=1 \
    OPENAI_MODEL=gpt-6-luna \
    STATE_DIR=/data \
    DAILY_USD_CAP=1.00 \
    SESSION_MSG_CAP=10 \
    RATE_PER_MIN=6 \
    PYTHONUNBUFFERED=1

# OPENAI_API_KEY is injected by the host at runtime. It is never baked into the
# image, and limits.clean_env() keeps it out of the agent's shell as well.

EXPOSE 8000
ENTRYPOINT ["python3", "/app/entrypoint.py"]
CMD ["sh", "-c", "uvicorn server:app --host 0.0.0.0 --port ${PORT:-8000}"]
