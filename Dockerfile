# syntax=docker/dockerfile:1
FROM python:3.12-slim

# Non-root runtime user -- the container never needs root once dependencies
# are installed.
RUN groupadd --system app \
    && useradd --system --gid app --create-home --home-dir /home/app app

WORKDIR /app

# Install dependencies in their own layer so code changes don't invalidate
# the (slow) pip install layer cache.
COPY requirements.txt ./
RUN pip install --no-cache-dir --upgrade pip \
    && pip install --no-cache-dir -r requirements.txt

# Application code. Note data/raw/ (the source PDFs) is deliberately NOT
# copied in -- the container only needs the already-built index, not the
# ingestion inputs. Re-run `python ingest.py` and rebuild the image when the
# source documents change.
COPY backend/ ./backend/
COPY frontend/ ./frontend/
COPY mcp_server.py ./
COPY data/index/ ./data/index/

RUN chown -R app:app /app
USER app

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

EXPOSE 8080

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8080/healthz', timeout=3).status == 200 else 1)"

CMD ["uvicorn", "backend.app.main:app", "--host", "0.0.0.0", "--port", "8080"]
