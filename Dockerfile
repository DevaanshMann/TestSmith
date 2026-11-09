# TestSmith: Streamlit app + sandboxed pytest runner.
# Works on Render (which sets $PORT) and anywhere else Docker runs.
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

# Unprivileged user that runs the app and every sandboxed test process.
RUN useradd --create-home --uid 10001 app

WORKDIR /app
COPY requirements.txt .
RUN pip install -r requirements.txt

# Code stays owned by root, so the app user (and any test it runs) cannot modify it.
COPY . .
RUN mkdir -p /app/logs && chown app:app /app/logs

USER app
ENV PORT=8501 \
    TESTSMITH_AUDIT_LOG=/app/logs/audit.jsonl
EXPOSE 8501

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s \
    CMD python -c "import os, urllib.request; urllib.request.urlopen(f'http://127.0.0.1:{os.environ[\"PORT\"]}/_stcore/health', timeout=4)"

CMD ["sh", "-c", "exec streamlit run app.py --server.port=${PORT} --server.address=0.0.0.0 --server.headless=true"]
