# Personal research agent for Dodil Ignite (BYOI / Dockerfile deployment).
#
# Ignite contract honoured here:
#   * the container is its own entrypoint and serves HTTP on $PORT (default 80)
#   * GET /healthz answers 200 for the readiness/liveness probe
#   * runs as a non-root user; only $HOME (/tmp/ignite) is writable at runtime
#
# Everything the agent talks to (Ignite chat completions, K3 memory + vector
# search, arXiv) is reached over plain HTTPS with a service-account token, so the
# image stays tiny — just Python + httpx.
FROM python:3.12-slim

RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY app/ ./app/

RUN mkdir -p /tmp/ignite && chown -R 65534:65534 /tmp/ignite
ENV HOME=/tmp/ignite \
    PYTHONUNBUFFERED=1 \
    PORT=80

EXPOSE 80
USER 65534:65534

CMD ["sh", "-c", "uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-80}"]
