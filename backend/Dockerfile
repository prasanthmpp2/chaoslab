FROM python:3.12-slim AS base
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1
WORKDIR /srv
RUN apt-get update && apt-get install -y --no-install-recommends curl ca-certificates && rm -rf /var/lib/apt/lists/*
COPY pyproject.toml ./
COPY app ./app
COPY alembic.ini ./
RUN pip install .
# Optional: install a Pumba binary. Supply a URL you have verified for your platform, e.g.
#   docker compose build --build-arg PUMBA_URL=https://github.com/alexei-led/pumba/releases/download/<tag>/<asset>
# If omitted, the Pumba adapter honestly reports itself as unavailable.
COPY scripts/pumba /usr/local/bin/pumba
RUN chmod +x /usr/local/bin/pumba
ARG PUMBA_URL=""
RUN if [ -n "$PUMBA_URL" ]; then curl -fsSL "$PUMBA_URL" -o /usr/local/bin/pumba && chmod +x /usr/local/bin/pumba; fi
RUN useradd -r -u 10001 chaos && mkdir -p /var/lib/chaos/artifacts && chown -R chaos /var/lib/chaos
USER chaos
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
