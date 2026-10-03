ARG PYTHON_BASE_IMAGE=docker.io/library/python:3.13.16-slim-bookworm
FROM ${PYTHON_BASE_IMAGE}

ARG ATLAS_VERSION=development

LABEL org.opencontainers.image.title="Aricoma Atlas backend" \
      org.opencontainers.image.version="${ATLAS_VERSION}"

ENV HOME=/tmp \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1

RUN groupadd --gid 10001 atlas \
    && useradd --uid 10001 --gid atlas --no-create-home atlas

WORKDIR /app

COPY requirements.txt /app/requirements.txt
RUN python -m pip install --requirement /app/requirements.txt

COPY --chown=atlas:atlas atlas /app/atlas
COPY --chown=atlas:atlas catalog /app/catalog

USER 10001:10001

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=3s --start-period=10s --retries=3 \
    CMD ["python", "-c", "import json,urllib.request; response=urllib.request.urlopen('http://127.0.0.1:8000/api/health', timeout=2); data=json.load(response); assert response.status == 200 and data.get('status') == 'ok'"]

CMD ["python", "-m", "uvicorn", "atlas.api.main:app", "--host", "0.0.0.0", "--port", "8000", "--no-access-log"]
