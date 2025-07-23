FROM nvidia/cuda:12.1.1-cudnn8-devel-ubuntu22.04

# Set non-interactive frontend for package installers
ENV DEBIAN_FRONTEND=noninteractive

# Install essential system dependencies including FFmpeg and Python
RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    python3.10 \
    python3-pip \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Make python3.10 the default python3
RUN ln -sf /usr/bin/python3.10 /usr/bin/python3

# Set the working directory
WORKDIR /app

# Copy requirements file and install Python dependencies
COPY requirements.txt .

# Install PyTorch with CUDA 12.1 support
RUN pip3 install --no-cache-dir torch torchvision torchaudio --index-url https://download.pytorch.org/whl/cu121

# Install the remaining Python packages
RUN pip3 install --no-cache-dir -r requirements.txt

# Set a predictable cache directory for Hugging Face models
ENV HUGGINGFACE_HUB_CACHE=/app/hf_cache

# Create the cache directory
RUN mkdir -p /app/hf_cache

# Copy the application code
COPY *.py /app/

# Create a startup script that handles model downloading
COPY startup.sh /app/
RUN chmod +x /app/startup.sh

# Use the startup script as the entrypoint
ENTRYPOINT ["/app/startup.sh"]
CMD ["python3", "main.py"]