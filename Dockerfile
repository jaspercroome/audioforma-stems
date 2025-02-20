FROM pytorch/pytorch:2.1.0-cuda11.8-cudnn8-runtime

WORKDIR /app

# Prevent timezone prompt during build
ENV DEBIAN_FRONTEND=noninteractive
ENV TZ=UTC

# Install system dependencies
RUN apt-get update && \
    apt-get install -y --no-install-recommends \
    ffmpeg \
    git \
    tzdata \
    wget \
    && ln -snf /usr/share/zoneinfo/$TZ /etc/localtime && echo $TZ > /etc/timezone \
    && rm -rf /var/lib/apt/lists/*

# Install Python dependencies
RUN pip install --no-cache-dir \
    "numpy<2.0" \
    fastapi==0.109.2 \
    uvicorn==0.27.1 \
    python-multipart==0.0.9 \
    aiohttp==3.9.3 \
    aiofiles==23.2.1 \
    pydantic==2.6.1 \
    python-dotenv==1.0.1 \
    pydub==0.25.1 \
    python-jose==3.3.0 \
    supabase==2.3.4 \
    demucs==4.0.1

# Set up directories and environment
RUN mkdir -p /root/.cache/torch/hub/checkpoints temp temp_uploads

# Copy download script first
COPY download_model.py .

# Download model with timeout and retries
RUN timeout 600 python download_model.py || (echo "Model download timed out" && exit 1)

# Copy application code
COPY . .

# Set environment variables
ENV PYTHONUNBUFFERED=1
ENV PORT=8000
ENV TORCH_HOME=/root/.cache/torch

# Expose the port
EXPOSE $PORT

# Start command
CMD uvicorn src.app:app --host 0.0.0.0 --port $PORT