# One container: the React build is served by FastAPI from the same origin.

# --- Build the frontend -------------------------------------------------------
FROM node:22-slim AS web
WORKDIR /web
COPY web/package.json web/package-lock.json ./
RUN npm ci
COPY web/ ./
RUN npm run build

# --- Run the API ---------------------------------------------------------------
# The Playwright image ships the browsers (in /ms-playwright) and their system
# libraries, but not the Python package. It is installed below pinned to the
# image's own version, because a pip-resolved playwright would expect a
# different browser build than the one bundled. Keep both in step with
# `pip show playwright` locally.
FROM mcr.microsoft.com/playwright/python:v1.62.0-noble
WORKDIR /app

# A real (non-editable) install: the wheel carries schemas/ and the ontology
# as package data, so nothing depends on the source tree at runtime.
COPY pyproject.toml README.md marketplace.json ./
COPY schemas ./schemas
COPY skills ./skills
COPY src ./src
RUN pip install --no-cache-dir ".[web]" "playwright==1.62.0"

COPY --from=web /web/dist ./web/dist

ENV BRAIAUDIT_WEB_DIST=/app/web/dist \
    BRAIAUDIT_DB=/data/braiaudit.db \
    BRAIAUDIT_MAX_RENDER_PAGES=3
VOLUME /data
EXPOSE 8000
CMD ["uvicorn", "braiaudit.server:app", "--host", "0.0.0.0", "--port", "8000"]
