FROM mcr.microsoft.com/playwright/python:v1.49.0-noble

# Install Tesseract OCR
RUN apt-get update && \
    apt-get install -y --no-install-recommends tesseract-ocr && \
    rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Playwright browsers are already installed in the base image

COPY . .

# Create data directories
RUN mkdir -p data/uploads data/results

EXPOSE 8000

# Render sets $PORT env var; default to 8000 locally
CMD uvicorn app:app --host 0.0.0.0 --port ${PORT:-8000}
