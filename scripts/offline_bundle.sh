#!/bin/sh
set -eu
mode=${1:?prepare或load}
destination=${2:?独立输出目录}
if [ "$mode" = prepare ]; then
  command -v docker >/dev/null
  docker info >/dev/null
  test ! -e "$destination"
  mkdir -m 700 -p "$destination"
  docker compose -f deploy/compose.yaml --env-file "${QUALITY_CUSTOMER_ENV:?客户env文件}" build
  docker pull pgvector/pgvector:0.8.7-pg17-trixie
  docker pull rabbitmq:4.3.6-management
  docker pull chrislusf/seaweedfs:4.48
  docker pull jaegertracing/jaeger:2.14.1
  docker pull caddy:2.10.2-alpine
  docker save -o "$destination/images.tar" quality-platform:0.2.0rc1 pgvector/pgvector:0.8.7-pg17-trixie rabbitmq:4.3.6-management chrislusf/seaweedfs:4.48 jaegertracing/jaeger:2.14.1 caddy:2.10.2-alpine
  # Obtain wheels for the built Linux image's Python/architecture, not the host Mac.
  destination_absolute=$(cd "$destination" && pwd)
  docker run --rm --user "$(id -u):$(id -g)" -v "$destination_absolute:/out" quality-platform:0.2.0rc1 python -m pip download --only-binary=:all: -r /app/requirements.txt -d /out/wheels
  docker run --rm --user "$(id -u):$(id -g)" -v "$destination_absolute:/out" quality-platform:0.2.0rc1 python scripts/release_manifest.py /out/runtime-manifest.json
  python3 scripts/release_manifest.py "$destination/manifest.json"
  python3 scripts/bundle_checksums.py write "$destination"
elif [ "$mode" = load ]; then
  python3 scripts/bundle_checksums.py verify "$destination"
  docker load -i "$destination/images.tar"
else
  echo "仅支持prepare/load" >&2
  exit 2
fi
