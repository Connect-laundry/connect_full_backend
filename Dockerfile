# Production Dockerfile
FROM python:3.12-slim as builder

WORKDIR /app

ENV PYTHONDONTWRITEBYTECODE 1
ENV PYTHONUNBUFFERED 1
ENV PIP_ROOT_USER_ACTION ignore

RUN apt-get update && apt-get install -y \
    build-essential \
    libpq-dev \
    libffi-dev \
    libssl-dev \
    binutils \
    libproj-dev \
    gdal-bin \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip wheel --no-cache-dir --wheel-dir /app/wheels -r requirements.txt

# Final stage
FROM python:3.12-slim

WORKDIR /app
ENV PIP_ROOT_USER_ACTION ignore

RUN apt-get update && apt-get install -y \
    libpq-dev \
    binutils \
    libproj-dev \
    gdal-bin \
    curl \
    && rm -rf /var/lib/apt/lists/*

COPY --from=builder /app/wheels /wheels
COPY --from=builder /app/requirements.txt .
RUN pip install --no-cache /wheels/*

COPY . .

# Create a non-root user
RUN addgroup --system django && adduser --system --group django
RUN chown -R django:django /app
USER django

EXPOSE 8000

# `migrate_safely` holds a PostgreSQL advisory lock, so a rolling deploy or a
# scale-up cannot race the same migration across instances. Set
# RUN_MIGRATIONS_ON_START=false when migrations run as a separate release step.
#
# gthread: each worker serves GUNICORN_THREADS requests at once. Requests spend
# most of their time waiting on the database, so 2 sync workers (2 requests in
# the whole service) queued the ~9 calls the app makes when Home opens. Each
# thread keeps its own database connection (CONN_MAX_AGE), so workers x threads
# is the connection count; keep it within the database pooler's pool size.
# --max-requests recycles each worker after ~2000 requests so slow memory
# growth cannot build up on a 512 MB instance.
CMD bash -c "\
  if [ \"${RUN_MIGRATIONS_ON_START:-true}\" = \"true\" ]; then \
    python manage.py migrate_safely || exit 1; \
  fi && \
  python manage.py collectstatic --noinput && \
  gunicorn --bind 0.0.0.0:${PORT:-8000} --workers ${GUNICORN_WORKERS:-2} --worker-class gthread --threads ${GUNICORN_THREADS:-4} --timeout 120 --max-requests ${GUNICORN_MAX_REQUESTS:-2000} --max-requests-jitter 200 config.wsgi:application"
