# SOTA Dockerfile for Vegetation Prime Backend
# Optimized for GIS performance, security, and build reliability

FROM python:3.11-slim

# 1. Environment Variables
# GDAL and Python configuration
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app \
    CPLUS_INCLUDE_PATH=/usr/include/gdal \
    C_INCLUDE_PATH=/usr/include/gdal \
    DEBIAN_FRONTEND=noninteractive

# 2. System Dependencies
# Install only what's strictly necessary for GDAL/Rasterio and production stability
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    g++ \
    libgdal-dev \
    gdal-bin \
    libgeos-dev \
    libproj-dev \
    libspatialindex-dev \
    postgresql-client \
    curl \
    && rm -rf /var/lib/apt/lists/*

# 3. Security: Non-root user creation
# Best practice for Kubernetes/OpenShift deployments
RUN groupadd -r appgroup && useradd -r -g appgroup -s /sbin/nologin -d /app appuser

# Set working directory
WORKDIR /app

# 4. Python Dependencies (Single Source of Truth)
# Copy requirements first to leverage Docker layer caching
COPY backend/requirements.txt ./requirements.txt
RUN pip install --no-cache-dir --upgrade pip setuptools wheel && \
    pip install --no-cache-dir -r requirements.txt

# 5. Application Code and Assets
# Copy code with appropriate ownership
COPY --chown=appuser:appgroup backend/app ./app
COPY --chown=appuser:appgroup backend/migrations ./migrations
COPY --chown=appuser:appgroup backend/scripts/run_migrations.py ./scripts/run_migrations.py
COPY --chown=appuser:appgroup backend/entrypoint.sh ./entrypoint.sh

# Ensure scripts are executable
RUN chmod +x /app/entrypoint.sh /app/scripts/run_migrations.py

# 6. Final Permissions and User Switch
# Ensure appuser owns the workdir (needed for temporary file processing)
RUN chown -R appuser:appgroup /app

# Switch to non-root user
USER appuser

# Expose FastAPI port
EXPOSE 8000

# Entrypoint handles migrations and starts the Uvicorn server
ENTRYPOINT ["/app/entrypoint.sh"]
