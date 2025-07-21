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
# This is done in stages to leverage Docker's layer caching
COPY requirements.txt .

# Install PyTorch with CUDA 12.1 support. This is a large dependency and is best installed separately.
RUN pip3 install --no-cache-dir torch torchvision torchaudio --index-url https://download.pytorch.org/whl/cu121

# Install the remaining Python packages
RUN pip3 install --no-cache-dir -r requirements.txt

# Set a predictable cache directory for Hugging Face models inside the container.
# This makes it easier for the application to load the model from a known path.
ENV HUGGINGFACE_HUB_CACHE=/app/hf_cache

# "Bake" the Pali-Gemma model into the image to avoid downloading it at runtime.
# A Hugging Face token must be passed as a build argument.
# Use a build-time secret mount to securely provide the Hugging Face token.
# This prevents the token from being stored in any image layer or history.
RUN --mount=type=secret,id=huggingface_token \
    huggingface-cli login --token $(cat /run/secrets/huggingface_token) && huggingface-cli download google/paligemma2-3b-mix-224

# Copy the application code into the container
COPY *.py /app/

# Set the default command to run when the container starts.
# This runs the main orchestrator script.
CMD ["python3", "main.py"]