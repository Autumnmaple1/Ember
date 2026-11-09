FROM node:22-alpine@sha256:fe4f6d74e20370acb04ad889077d14d0f64a9c8ea6a42bc60cddf1e844699914 AS frontend
WORKDIR /build/frontend
COPY frontend/package*.json ./
RUN npm ci
COPY frontend/ ./
RUN VITE_API_BASE_URL=/ npm run build

FROM postgres:16-bookworm@sha256:e8a07d2c7579b038938cdba6c539e580e5001caf9ea203d397b5cc83e76bd938 AS pgclient
FROM python:3.11-slim-bookworm@sha256:94a8fee35252d06ab75856f15f12c7bae67b10e8a6da93c7fded82b2610926cd
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PYTHONPATH=/app \
    PATH=/usr/lib/postgresql/16/bin:$PATH EMBER_RUNTIME_DIR=/runtime \
    EMBER_FRONTEND_DIR=/app/frontend/dist EMBER_NO_DOTENV=1
RUN apt-get update && apt-get install -y --no-install-recommends libpq5 liblz4-1 libzstd1 libreadline8 ca-certificates \
    && rm -rf /var/lib/apt/lists/*
COPY --from=pgclient /usr/lib/postgresql/16/bin/ /usr/lib/postgresql/16/bin/
WORKDIR /app
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt
COPY brain/ brain/
COPY core/ core/
COPY memory/ memory/
COPY persona/ persona/
COPY tools/ tools/
COPY archive/ archive/
COPY service/ service/
COPY config/settings.py config/dialogue_policy.py config/logging_config.py config/prompts.yaml config/state_default.json config/
COPY server.py ./
COPY --from=frontend /build/frontend/dist/ frontend/dist/
RUN useradd --uid 10001 --create-home ember && mkdir /runtime && chown ember:ember /runtime
USER ember
WORKDIR /runtime
EXPOSE 8000
CMD ["python", "-m", "service.run"]
