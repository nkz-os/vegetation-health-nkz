# Optimized Dockerfile for faster builds in GHCR
# Key optimizations:
# 1. Layer caching: Install system deps first (rarely change)
# 2. Separate requirements layers: Base deps vs geospatial deps
# 3. Copy code last to maximize cache hits

FROM python:3.11-slim

# Set working directory
WORKDIR /app

# Install system dependencies (this layer rarely changes, good for cache)
RUN apt-get update && apt-get install -y \
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

# Set GDAL environment variables
ENV CPLUS_INCLUDE_PATH=/usr/include/gdal
ENV C_INCLUDE_PATH=/usr/include/gdal

# Upgrade pip and install build tools (cached layer)
RUN pip install --no-cache-dir --upgrade pip setuptools wheel

# Copy only requirements first (for better layer caching)
COPY backend/requirements.txt ./requirements.txt

# Install Python dependencies in stages for better caching
# Stage 1: Base dependencies (change less frequently)
RUN pip install --no-cache-dir \
    fastapi==0.104.1 \
    uvicorn[standard]==0.24.0 \
    python-multipart==0.0.6 \
    pydantic==2.5.0 \
    pydantic-settings==2.1.0 \
    sqlalchemy==2.0.23 \
    psycopg2-binary==2.9.9 \
    geoalchemy2==0.14.2 \
    alembic==1.12.1 \
    celery==5.3.4 \
    redis==5.0.1 \
    flower==2.0.1 \
    pyjwt==2.8.0 \
    cryptography==41.0.7 \
    numpy==1.24.3 \
    Pillow==10.1.0 \
    boto3==1.29.7 \
    botocore==1.32.7 \
    simpleeval==0.9.13 \
    httpx==0.25.2 \
    requests==2.31.0 \
    python-dotenv==1.0.0 \
    python-dateutil==2.8.2 \
    structlog==23.2.0

# Stage 2: Geospatial dependencies
# Note: fiona and geopandas removed - not used in code, only rasterio is needed
# This significantly reduces build time and eliminates ARM64 compilation issues
RUN pip install --no-cache-dir \
    shapely==2.0.2 \
    pyproj==3.6.1 \
    rasterio==1.3.9

# Stage 3: Sentinel-2 and remaining deps
RUN pip install --no-cache-dir \
    pystac-client==0.7.0 \
    sentinelsat==1.2.1 \
    rio-cogeo==5.1.0 \
    rasterstats==0.19.0 \
    pytest==7.4.3 \
    pytest-asyncio==0.21.1 \
    pytest-cov==4.1.0 \
    scipy==1.11.4 \
    scikit-learn==1.3.2 \
    pyarrow==15.0.2 \
    pyarrow==15.0.2

# Copy application code (this invalidates cache most often)
COPY backend/app ./app
COPY backend/migrations ./migrations

# Create scripts directory if it doesn't exist
RUN mkdir -p /app/scripts

# Copy migration script
COPY backend/scripts/run_migrations.py /app/scripts/run_migrations.py
RUN chmod +x /app/scripts/run_migrations.py

# Copy entrypoint script
COPY backend/entrypoint.sh /app/entrypoint.sh
RUN chmod +x /app/entrypoint.sh

# Set Python path
ENV PYTHONPATH=/app

# Expose port
EXPOSE 8000

# Use entrypoint script (runs migrations then starts app)
ENTRYPOINT ["/app/entrypoint.sh"]

