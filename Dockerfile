# EnergyScheduler — single-container image for Hugging Face Spaces (Docker SDK).
# One uvicorn process serves the FastAPI API and the mounted Gradio UI on port 7860.
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# Install dependencies first for better layer caching.
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy the application code.
COPY . .

# Hugging Face Spaces routes external traffic to port 7860.
EXPOSE 7860

# API at /*, UI at /ui (root redirects to /ui).
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "7860"]
