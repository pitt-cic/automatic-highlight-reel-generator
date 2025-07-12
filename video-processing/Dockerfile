FROM python:3.9-slim

# Install system dependencies if needed
RUN apt-get update && apt-get install -y \
    && rm -rf /var/lib/apt/lists/*

# Set working directory
WORKDIR /app

# Copy application files
COPY script.py /app/script.py

# Set entry point
CMD ["python3", "script.py"]