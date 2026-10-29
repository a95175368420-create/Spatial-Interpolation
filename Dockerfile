# syntax=docker/dockerfile:1
FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 MPLCONFIGDIR=/tmp/matplotlib XDG_CACHE_HOME=/tmp/cache
WORKDIR /app
RUN apt-get update && apt-get install -y --no-install-recommends fonts-noto-cjk && rm -rf /var/lib/apt/lists/*
COPY requirements.lock ./
RUN --mount=type=secret,id=proxy_ca \
    if [ -f /run/secrets/proxy_ca ]; then \
      PIP_CERT=/run/secrets/proxy_ca pip install --no-cache-dir -r requirements.lock; \
    else pip install --no-cache-dir -r requirements.lock; fi
COPY pyproject.toml README.md ./
COPY src ./src
RUN --mount=type=secret,id=proxy_ca \
    if [ -f /run/secrets/proxy_ca ]; then \
      PIP_CERT=/run/secrets/proxy_ca pip install --no-cache-dir --no-deps .; \
    else pip install --no-cache-dir --no-deps .; fi \
    && useradd --uid 10001 --create-home spatial
USER spatial
EXPOSE 8000
CMD ["spatial-monitor", "api", "--address", "0.0.0.0", "--port", "8000"]
