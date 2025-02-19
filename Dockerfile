FROM --platform=linux/amd64 continuumio/miniconda3:latest

WORKDIR /app

# Install system dependencies
RUN apt-get update && \
    apt-get install -y --no-install-recommends \
    build-essential \
    ffmpeg \
    && rm -rf /var/lib/apt/lists/*

# Create conda environment with just Python first
RUN conda create -n audioforma-stems python=3.9.18 -y

# Install conda packages in smaller groups
RUN conda install -n audioforma-stems -y \
    pip=23.3.1 \
    fastapi=0.109.0 \
    uvicorn=0.27.0 \
    python-multipart=0.0.7 \
    && conda clean -afy

RUN conda install -n audioforma-stems -y \
    pydub=0.25.1 \
    python-dotenv=1.0.0 \
    celery=5.3.6 \
    redis-py=5.0.1 \
    && conda clean -afy

RUN conda install -n audioforma-stems -y \
    ffmpeg=6.1.1 \
    requests=2.31.0 \
    aiohttp=3.9.1 \
    aiofiles=23.2.1 \
    && conda clean -afy

# Install pip packages
SHELL ["conda", "run", "-n", "audioforma-stems", "/bin/bash", "-c"]
RUN pip install demucs==4.0.1

# Copy application code
COPY . .

# Create necessary directories
RUN mkdir -p temp jobs

# Set environment variables
ENV PYTHONUNBUFFERED=1
ENV PORT=8000

# Expose the port
EXPOSE $PORT

# Start command using environment variable for port
CMD conda run -n audioforma-stems uvicorn src.app:app --host 0.0.0.0 --port $PORT