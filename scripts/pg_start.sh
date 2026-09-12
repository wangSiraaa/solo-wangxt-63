#!/usr/bin/env bash
# 启动用户目录下的 PostgreSQL（如数据目录不存在则先初始化并启用 PostGIS）
set -euo pipefail
ROOT="${SANITATION_PGROOT:-/workspace/.pgroot}"
BIN="$ROOT/usr/lib/postgresql/15/bin"
DATA="${SANITATION_PGDATA:-/workspace/.pgdata}"
export LD_LIBRARY_PATH="$ROOT/usr/lib/aarch64-linux-gnu:$ROOT/usr/lib/aarch64-linux-gnu/hdf5/serial:$ROOT/usr/lib"

if [ ! -d "$DATA" ]; then
  mkdir -p /tmp/pgsocket
  "$BIN/initdb" -D "$DATA" -U postgres --auth=trust -E UTF8
  cat >> "$DATA/postgresql.conf" <<'EOF'
shared_buffers=16MB
work_mem=2MB
max_connections=20
unix_socket_directories='/tmp/pgsocket'
port=54329
listen_addresses='127.0.0.1'
EOF
fi

"$BIN/pg_ctl" -D "$DATA" -l /workspace/.pgdata.log -w start

"$BIN/psql" -h 127.0.0.1 -p 54329 -U postgres -tc \
  "SELECT 1 FROM pg_database WHERE datname='sanitation'" | grep -q 1 || \
  "$BIN/createdb" -h 127.0.0.1 -p 54329 -U postgres sanitation
"$BIN/psql" -h 127.0.0.1 -p 54329 -U postgres -d sanitation -c "CREATE EXTENSION IF NOT EXISTS postgis;"
