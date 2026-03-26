# Production Dockerfile
FROM python:3.12-slim as builder

WORKDIR /app

ENV PYTHONDONTWRITEBYTECODE 1
ENV PYTHONUNBUFFERED 1

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

# Create a non-root user with a home directory
RUN addgroup --system django && adduser --system --group --home /home/django django
RUN chown -R django:django /app /home/django
USER django

# Healthcheck to ensure the container is responding
HEALTHCHECK --interval=30s --timeout=10s --retries=3 \
  CMD curl -f http://localhost:${PORT}/api/v1/support/faqs/ || exit 1

# Render will set $PORT, but we provide a default for local testing
ENV PORT=10000
EXPOSE ${PORT}

# Use a shell script for entrypoint to handle migrations and static files
# Added --worker-tmp-dir /dev/shm to prevent heartbeat permission issues in Docker
CMD ["bash", "-c", "python manage.py migrate --noinput && python manage.py createcachetable && python manage.py collectstatic --noinput && gunicorn --bind 0.0.0.0:${PORT} --workers 2 --timeout 120 --worker-tmp-dir /dev/shm config.wsgi:application"]
