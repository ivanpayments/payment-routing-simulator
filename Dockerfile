FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential libpq-dev curl \
    && rm -rf /var/lib/apt/lists/*

RUN groupadd -r app && useradd -r -g app -u 1001 app

WORKDIR /app

COPY pyproject.toml README.md ./
COPY payment_router ./payment_router
COPY scripts ./scripts

RUN pip install --upgrade pip && pip install -e .

RUN chown -R app:app /app
USER app

EXPOSE 8090

CMD ["uvicorn", "payment_router.api:app", "--host", "0.0.0.0", "--port", "8090"]
