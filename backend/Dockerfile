# Backend image: Python 3.11 + uv, dependencies installed from uv.lock.
FROM python:3.11.15-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_VERSION=0.11.16

RUN pip install --no-cache-dir "uv==${UV_VERSION}"

WORKDIR /app

COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project

COPY alembic.ini ./
COPY migrations ./migrations
COPY app ./app
COPY scripts ./scripts
# Versioned semantic + metric metadata (spec sections 10.2, 15) is loaded by the
# backend at bootstrap/context time; it lives at the repository root and is
# supplied as an additional build context named `metadata`.
COPY --from=metadata . ./metadata

ENV PATH="/app/.venv/bin:${PATH}"

EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
