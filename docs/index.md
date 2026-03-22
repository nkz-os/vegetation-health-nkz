---
title: Vegetation Health Module
description: Real-time biophysical inference engine for the Nekazari platform using satellite imagery.
sidebar:
  order: 1
---

# Vegetation Health

The **Vegetation Health Module** (formerly known as Crop Health / Vegetation Prime) provides real-time processing of satellite imagery to compute biophysical indices (NDVI, EVI, MSAVI) over NGSI-LD `AgriParcel` entities.

## Features

- **Automated Ingestion**: Connects directly to satellite data providers (e.g., Copernicus, Sentinel).
- **Index Calculation**: Computes Normalized Difference Vegetation Index (NDVI) and other spectral models.
- **Time-Series Storage**: Routes temporal variations into the platform's TimescaleDB via Apache Arrow IPC.
- **3D Visualization**: Generates color-mapped raster overlays directly onto the CesiumJS globe on the frontend.

## Architecture

This module follows the standard Nekazari IIFE architecture:
1. **Frontend**: Bundled as `nekazari-module.js` and served statically via MinIO. Registered dynamically at runtime.
2. **Backend**: Python-based API (FastAPI) responsible for triggering the worker pipelines.
3. **Workers**: Celery/RabbitMQ based workers that download heavy TIF files and compute matrices via `rasterio` and `numpy`.
