FROM node:24-slim AS frontend
WORKDIR /frontend
COPY ui/package.json ui/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY ui/ ./
RUN npm run build

FROM python:3.14-slim
WORKDIR /app
COPY requirements-image.txt ./
RUN pip install --no-cache-dir --disable-pip-version-check -r requirements-image.txt "ag-ui-protocol>=0.1.14,<0.2"
COPY pyproject.toml ./
COPY src ./src
RUN pip install --no-cache-dir --disable-pip-version-check --no-deps . && useradd --create-home --uid 10001 ui
COPY --from=frontend /frontend/dist /app/static
USER ui
ENV PYTHONUNBUFFERED=1 UI_STATIC_DIR=/app/static ENABLE_SENSITIVE_DATA=false
EXPOSE 8080
CMD ["advisor-team-ui"]
