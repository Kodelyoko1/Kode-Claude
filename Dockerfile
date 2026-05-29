# ViralRecycler SaaS — Render/Fly/Railway container.
FROM python:3.11-slim

# System deps: ffmpeg for video work, build-essential for any wheel builds
RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    ca-certificates \
    curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Copy requirements first so layer caches well
COPY requirements-saas.txt ./
RUN pip install --no-cache-dir -r requirements-saas.txt

# Copy the app
COPY . .

# Render injects $PORT (typically 10000); app reads VR_PORT
ENV VR_PORT=10000
EXPOSE 10000

# Data dir for trials, queues, uploads, metrics
RUN mkdir -p /app/data && chmod 755 /app/data

# Run the SaaS server (worker thread starts automatically)
# Render's $PORT env var overrides VR_PORT at runtime via this wrapper
CMD ["sh", "-c", "python3 viral_recycler_server.py --port ${PORT:-${VR_PORT:-10000}}"]
