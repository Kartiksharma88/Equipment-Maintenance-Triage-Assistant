FROM python:3.12-slim

WORKDIR /srv

# Dependencies first (better layer caching)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt gunicorn==23.0.0

COPY app.py config.py ./
COPY static/ ./static/
COPY store/ ./store/
COPY triage/ ./triage/

# Runtime configuration (override in your host's dashboard, never bake secrets in)
ENV PORT=5000 \
    DATA_DIR=/srv/data \
    FLASK_DEBUG=0

EXPOSE 5000

# Gunicorn production server: 2 workers, generous timeout for Gemini calls
CMD ["sh", "-c", "gunicorn 'app:app' --bind 0.0.0.0:${PORT} --workers 2 --timeout 90"]
