FROM --platform=linux/amd64 python:3.9.18-slim as pytorch-base

# Install build dependencies
RUN apt-get update && \
    apt-get install -y --no-install-recommends \
    build-essential \
    git \
    && rm -rf /var/lib/apt/lists/*

# Install PyTorch and Demucs first
RUN pip install torch==2.1.0 torchaudio==2.1.0 --index-url https://download.pytorch.org/whl/cpu
RUN pip install demucs==4.0.1

# Pre-download Demucs models to avoid hanging during runtime
RUN python -c "from demucs.pretrained import get_model; get_model('mdx_extra')"

# Final stage
FROM --platform=linux/amd64 python:3.9.18-slim

WORKDIR /app

# Install runtime dependencies
RUN apt-get update && \
    apt-get install -y --no-install-recommends \
    ffmpeg \
    && rm -rf /var/lib/apt/lists/*

# Copy PyTorch and Demucs from base stage
COPY --from=pytorch-base /usr/local/lib/python3.9/site-packages/torch /usr/local/lib/python3.9/site-packages/torch
COPY --from=pytorch-base /usr/local/lib/python3.9/site-packages/torchaudio /usr/local/lib/python3.9/site-packages/torchaudio
COPY --from=pytorch-base /usr/local/lib/python3.9/site-packages/demucs /usr/local/lib/python3.9/site-packages/demucs
COPY --from=pytorch-base /root/.cache/torch/hub /root/.cache/torch/hub

# Copy and install requirements separately to avoid dependency resolver issues
COPY requirements.txt requirements-supabase.txt ./
RUN pip install -r requirements.txt && \
    pip install -r requirements-supabase.txt

# Copy application code
COPY . .

# Create necessary directories
RUN mkdir -p temp temp_uploads

# Set environment variables
ENV PYTHONUNBUFFERED=1
ENV PORT=8000

# Expose the port
EXPOSE $PORT

# Start command
CMD uvicorn src.app:app --host 0.0.0.0 --port $PORT