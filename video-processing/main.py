import os
import sys
import logging
import tempfile
import traceback
import time
from pathlib import Path

import boto3
from botocore.exceptions import ClientError
import pandas as pd

# Import the refactored processing functions
from downsample_videos import run_downsampling
from run_inference_and_postprocess import run_inference
from clipping_and_merging import run_clipping
from config_loader import config # Import the loaded config

# --- Configuration ---
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] [%(name)s] %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
log = logging.getLogger(__name__)

# --- Main Orchestrator ---
def main():
    """
    Main orchestrator for the video highlight generation pipeline.
    """
    pipeline_start_time = time.time()
    log.info("=== Video Highlight Processor Starting ===")

    # 1. Get Configuration from Environment Variables and Config File
    s3_bucket = os.environ.get("S3_BUCKET")
    s3_key = os.environ.get("S3_KEY")
    # Use prompt from env var, or fall back to config file's default
    event_prompt = os.environ.get("EVENT_PROMPT", config['main']['default_prompt'])
    skip_inference_test = os.environ.get("SKIP_INFERENCE_TEST", "false").lower() == "true"

    if not s3_bucket or not s3_key:
        log.error("S3_BUCKET and S3_KEY environment variables are required.")
        sys.exit(1)

    log.info(f"Processing s3://{s3_bucket}/{s3_key}")
    log.info(f"Using event prompt: '{event_prompt}'")

    s3_client = boto3.client("s3")

    with tempfile.TemporaryDirectory() as temp_dir_str:
        temp_dir = Path(temp_dir_str)
        log.info(f"Created temporary working directory: {temp_dir}")

        try:
            original_video_filename = Path(s3_key).name
            local_video_path = temp_dir / original_video_filename
            log.info(f"Downloading video to {local_video_path}...")
            s3_client.download_file(s3_bucket, s3_key, str(local_video_path))

            # --- STAGE 1: DOWNSAMPLING ---
            stage1_start = time.time()
            downsampled_video_path, timestamps_csv_path = run_downsampling(
                input_video_path=local_video_path,
                output_dir=temp_dir,
                target_fps=config['downsampling']['target_fps']
            )
            log.info(f"--- Stage 1 (Downsampling) completed in {time.time() - stage1_start:.2f}s ---")

            # --- STAGE 2: INFERENCE & POST-PROCESSING ---
            stage2_start = time.time()
            if skip_inference_test:
                log.warning("SKIP_INFERENCE_TEST is set to true. Skipping inference.")
                # Create a dummy intervals CSV for testing the clipping stage
                mock_intervals = [{"start": 5.0, "end": 10.0}, {"start": 25.0, "end": 30.0}]
                video_stem = local_video_path.stem
                predicted_intervals_csv_path = temp_dir / f"{video_stem}_predicted_intervals.csv"
                pd.DataFrame(mock_intervals).to_csv(predicted_intervals_csv_path, index=False)
                log.info(f"Created mock intervals file at: {predicted_intervals_csv_path}")
            else:
                predicted_intervals_csv_path = run_inference(
                    downsampled_video_path=downsampled_video_path,
                    timestamps_csv_path=timestamps_csv_path,
                    output_dir=temp_dir,
                    prompt=event_prompt,
                    inference_config=config['inference'],
                    post_proc_config=config['post_processing'],
                    # FIX: Added the missing target_fps argument
                    target_fps=config['downsampling']['target_fps']
                )
            log.info(f"--- Stage 2 (Inference) completed in {time.time() - stage2_start:.2f}s ---")

            # --- STAGE 3: CLIPPING & MERGING ---
            stage3_start = time.time()
            final_video_path = run_clipping(
                original_video_path=local_video_path,
                predicted_intervals_csv_path=predicted_intervals_csv_path,
                output_dir=temp_dir,
                clipping_config=config['clipping']
            )
            log.info(f"--- Stage 3 (Clipping & Merging) completed in {time.time() - stage3_start:.2f}s ---")

            # 4. Upload the final result to S3
            if final_video_path and final_video_path.exists():
                output_s3_key = f"{config['main']['s3_output_prefix']}/{final_video_path.name}"
                log.info(f"Uploading final highlight video to s3://{s3_bucket}/{output_s3_key}")
                s3_client.upload_file(str(final_video_path), s3_bucket, output_s3_key)
            else:
                log.warning("No final video was generated. Skipping upload.")

        except ClientError as e:
            log.error(f"An S3 error occurred: {e.response['Error']['Message']}")
            log.error(traceback.format_exc())
            sys.exit(1)
        except Exception as e:
            log.error(f"An unexpected error occurred: {e}")
            log.error(traceback.format_exc())
            sys.exit(1)

    log.info(f"=== Video Highlight Processor Finished Successfully in {time.time() - pipeline_start_time:.2f}s ===")


if __name__ == "__main__":
    main()