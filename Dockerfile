FROM node:24.19.0-alpine AS frontend
WORKDIR /build
COPY frontend/package.json frontend/pnpm-lock.yaml ./
RUN npm install -g pnpm@11.25.0 --ignore-scripts && pnpm install --frozen-lockfile --ignore-scripts
COPY frontend/ ./
RUN pnpm build

FROM python:3.12.14-slim-trixie AS application
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app
# PG17 client must match the deployed server major for pg_dump/pg_restore.
RUN apt-get update && apt-get install -y --no-install-recommends postgresql-client-17=17.11-0+deb13u1 && apt-get clean
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt && useradd --uid 10001 --create-home quality
COPY quality/ quality/
COPY migrations/ migrations/
COPY alembic.ini pyproject.toml ./
COPY --from=frontend /build/dist frontend/dist/
COPY scripts/ scripts/
RUN mkdir -m 700 /data && chown quality:quality /data
USER 10001:10001
ENV QUALITY_DATA_DIR=/data
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/api/health',timeout=3)"
CMD ["python","-m","uvicorn","quality.app:app","--host","0.0.0.0","--port","8000","--proxy-headers","--forwarded-allow-ips=127.0.0.1"]
