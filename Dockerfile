FROM python:3.13-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        espeak-ng \
        ffmpeg \
        fonts-dejavu-core \
    && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml README.md ./
COPY app ./app
COPY alembic ./alembic
COPY alembic.ini ./
COPY config ./config
COPY docs ./docs
COPY frontend/src ./frontend/src

RUN pip install --no-cache-dir -e '.[veo]'

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
