# ─────────────────────────────────────────────────────────────────────────────
# Cloud Cost Optimizer — Dockerfile
# ─────────────────────────────────────────────────────────────────────────────

FROM python:3.11-slim AS base

# Metadata
LABEL maintainer="Cloud Cost Optimizer"
LABEL description="Automated cloud cost optimization pipeline"

# Prevent Python from writing .pyc files and enable unbuffered output
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

# Set working directory
WORKDIR /app

# Install system dependencies
RUN apt-get update && \
    apt-get install -y --no-install-recommends \
        gcc \
        curl \
    && rm -rf /var/lib/apt/lists/*

# Install Python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir -r requirements.txt

# Copy application source
COPY . .

# Health check — verify Python can import the scheduler
HEALTHCHECK --interval=60s --timeout=10s --start-period=30s --retries=3 \
    CMD python -c "from scheduler.scheduler import main; print('OK')" || exit 1

# Default command: run the scheduler
CMD ["python", "-m", "scheduler.scheduler"]
