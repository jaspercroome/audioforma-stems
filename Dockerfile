FROM --platform=linux/amd64 python:3.9.18-slim

WORKDIR /app

# Install system dependencies
RUN apt-get update && \
    apt-get install -y --no-install-recommends \
    build-essential \
    ffmpeg \
    git \
    && rm -rf /var/lib/apt/lists/*

# Install PyTorch first (CPU version to keep image smaller)
RUN pip install torch==2.1.0 torchaudio==2.1.0 --index-url https://download.pytorch.org/whl/cpu

# Install Demucs
RUN pip install demucs==4.0.1

# Install web application dependencies
RUN pip install \
    fastapi==0.104.1 \
    uvicorn==0.24.0 \
    python-multipart==0.0.6 \
    pydub==0.25.1 \
    python-dotenv==1.0.0

# Install async and file handling dependencies
RUN pip install \
    aiohttp==3.8.5 \
    aiofiles==22.1.0 \
    requests==2.31.0

# Install task queue dependencies
RUN pip install \
    celery==5.3.4 \
    redis==5.0.0

# Copy application code
COPY . .

# Create necessary directories
RUN mkdir -p temp jobs

# Set environment variables
ENV PYTHONUNBUFFERED=1
ENV PORT=8000

# Expose the port
EXPOSE $PORT

# Start command
CMD uvicorn src.app:app --host 0.0.0.0 --port $PORT