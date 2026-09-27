FROM python:3.11-slim

# Tesseract (lecture des courriers scannés) avec la langue française
RUN apt-get update \
    && apt-get install -y --no-install-recommends tesseract-ocr tesseract-ocr-fra \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .

ENV CRM_DATABASE_URL=sqlite:////data/crm.db \
    CRM_UPLOAD_FOLDER=/data/uploads \
    CRM_BEHIND_PROXY=1 \
    PORT=10000

# Un seul processus (SQLite) avec plusieurs fils ; délai long pour l'OCR.
CMD mkdir -p /data/uploads && exec gunicorn run:app \
    --bind 0.0.0.0:${PORT} --workers 1 --threads 8 --timeout 180
