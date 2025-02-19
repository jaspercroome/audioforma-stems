FROM --platform=linux/amd64 continuumio/miniconda3:latest

WORKDIR /app

# Install system dependencies
RUN apt-get update && \
    apt-get install -y --no-install-recommends \
    build-essential \
    ffmpeg \
    && rm -rf /var/lib/apt/lists/*

# Copy only requirements first to leverage Docker cache
COPY environment.yml .
RUN conda env create -f environment.yml

# Make RUN commands use the new environment
SHELL ["conda", "run", "-n", "audioforma-stems", "/bin/bash", "-c"]

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