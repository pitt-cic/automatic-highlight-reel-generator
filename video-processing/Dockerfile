FROM nvidia/cuda:12.1.1-cudnn8-devel-ubuntu22.04

# Set non-interactive frontend for package installers
ENV DEBIAN_FRONTEND=noninteractive

# Install essential system dependencies including FFmpeg and Python
RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    python3.10 \
    python3-pip \
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

# "Bake" the Pali-Gemma model into the image to avoid downloading it at runtime.
# A Hugging Face token must be passed as a build argument.
# The Hugging Face token is passed as an environment variable from the ECS task definition.
ARG HUGGINGFACE_TOKEN
RUN huggingface-cli login --token ${HUGGINGFACE_TOKEN} && huggingface-cli download google/paligemma2-3b-mix-224

# Copy the application code into the container
COPY *.py /app/

# Set the default command to run when the container starts.
# This runs the main orchestrator script.
CMD ["python3", "main.py"]