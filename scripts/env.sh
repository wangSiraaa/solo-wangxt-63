#!/usr/bin/env bash
# 使用方式: source scripts/env.sh
# 指向用户目录下解包的 PostgreSQL/PostGIS/GDAL/GEOS 运行环境
export SANITATION_PGROOT="${SANITATION_PGROOT:-/workspace/.pgroot}"
export LD_LIBRARY_PATH="$SANITATION_PGROOT/usr/lib/aarch64-linux-gnu:$SANITATION_PGROOT/usr/lib/aarch64-linux-gnu/hdf5/serial:$SANITATION_PGROOT/usr/lib${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}"
export GDAL_LIBRARY_PATH="$SANITATION_PGROOT/usr/lib/aarch64-linux-gnu/libgdal.so.32"
export GEOS_LIBRARY_PATH="$SANITATION_PGROOT/usr/lib/aarch64-linux-gnu/libgeos_c.so.1"
export PATH="/workspace/.venv/bin:$SANITATION_PGROOT/usr/lib/postgresql/15/bin:$PATH"
export POSTGRES_HOST="${POSTGRES_HOST:-127.0.0.1}"
export POSTGRES_PORT="${POSTGRES_PORT:-54329}"
export POSTGRES_USER="${POSTGRES_USER:-postgres}"
export POSTGRES_DB="${POSTGRES_DB:-sanitation}"
