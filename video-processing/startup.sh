#!/bin/bash
set -e

echo "[STARTUP] Checking for Pali-Gemma model..."

# Check if the model already exists in the cache
if [ -d "$HUGGINGFACE_HUB_CACHE/models--google--paligemma2-3b-mix-224" ]; then
    echo "[STARTUP] Model already cached, skipping download."
else
    echo "[STARTUP] Model not found in cache. Downloading..."
    
    # Get the Hugging Face token from environment variable
    # This will be set by the ECS task definition
    if [ -z "$HUGGINGFACE_TOKEN" ]; then
        echo "[ERROR] HUGGINGFACE_TOKEN environment variable not set!"
        exit 1
    fi
    
    # Download the model using Python
    python3 -c "
import os
from transformers import AutoProcessor, PaliGemmaForConditionalGeneration

token = os.environ.get('HUGGINGFACE_TOKEN')
if not token:
    raise ValueError('HUGGINGFACE_TOKEN not set')

print('[STARTUP] Downloading Pali-Gemma model...')
model = PaliGemmaForConditionalGeneration.from_pretrained(
    'google/paligemma2-3b-mix-224',
    use_auth_token=token
)
processor = AutoProcessor.from_pretrained(
    'google/paligemma2-3b-mix-224',
    use_auth_token=token
)
print('[STARTUP] Model download complete.')
"
fi

echo "[STARTUP] Starting main application..."
# Execute the command passed to the container
exec "$@"
