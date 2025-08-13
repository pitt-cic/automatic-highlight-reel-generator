import os
import time
import subprocess
import logging
import pandas as pd
import torch
import torch.nn.functional as F
import cv2
from pathlib import Path
from PIL import Image
from transformers import AutoProcessor, PaliGemmaForConditionalGeneration
import threading
from tqdm import tqdm

log = logging.getLogger(__name__)

# --- Environment and Device Setup ---
os.environ["TORCHDYNAMO_DISABLE"] = "1"
torch.cuda.empty_cache()
device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
log.info(f"Using device: {device}")


# --- Model Loading ---
def load_model(model_id: str):
    """Loads the PaliGemma model and processor from the specified model_id."""
    log.info(f"Loading model '{model_id}' to device '{device}'...")
    start_time = time.time()
    processor = AutoProcessor.from_pretrained(model_id)
    model = PaliGemmaForConditionalGeneration.from_pretrained(
        model_id,
        torch_dtype=torch.float16,
        low_cpu_mem_usage=True,
    ).to(device)
    model.eval()
    log.info(f"Model loaded and ready in {time.time() - start_time:.2f}s.")
    return model, processor


# --- GPU Utilization Polling ---
polling = False
gpu_utils = []
def poll_gpu_utilization(interval=0.5):
    """Polls GPU utilization at a given interval."""
    global polling, gpu_utils
    while polling:
        try:
            output = subprocess.check_output(
                ['nvidia-smi', '--query-gpu=utilization.gpu', '--format=csv,noheader,nounits'],
                encoding='utf-8'
            )
            util = int(output.strip())
            gpu_utils.append(util)
        except Exception:
            gpu_utils.append(0) # Append 0 if nvidia-smi fails
        time.sleep(interval)

# --- Core Inference Function ---
def _run_inference_on_video(
    model, processor, video_path: Path, timestamps_df: pd.DataFrame, 
    prompt: str, batch_size: int, crop_coords: dict, max_new_tokens: int, target_fps: int
) -> pd.DataFrame:
    """Helper function to run the core inference loop on a video file."""
    inference_start_time = time.time()
    cap = cv2.VideoCapture(str(video_path))
    assert cap.isOpened(), f"Cannot open video: {video_path}"
    total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    log.info(f"Total frames to process: {total_frames}")

    # Start GPU polling
    global polling, gpu_utils
    polling = True
    gpu_utils.clear()
    poll_thread = threading.Thread(target=poll_gpu_utilization)
    poll_thread.start()

    results = []
    batch_images = []
    batch_frame_numbers = []
    
    pbar = tqdm(total=total_frames, desc="✨ Running Inference", unit="frame")

    def process_batch():
        """Processes a batch of frames."""
        nonlocal batch_images, batch_frame_numbers
        if not batch_images:
            return

        with torch.no_grad():
            inputs = processor(images=batch_images, text=[prompt] * len(batch_images), return_tensors="pt", padding=True).to(device)
            outputs = model.generate(
                **inputs,
                max_new_tokens=max_new_tokens, # Use config value
                output_scores=True,
                return_dict_in_generate=True,
            )

            answers = processor.batch_decode(outputs.sequences, skip_special_tokens=True)
            scores = outputs.scores
            generated_tokens = outputs.sequences

            for i in range(len(batch_images)):
                answer = answers[i].lower().strip().split("\n")[-1]
                token_probs = [
                    F.softmax(step_logits, dim=-1)[i, token_id.item()].item()
                    for step_logits, token_id in zip(scores, generated_tokens[i, -len(scores):])
                ]

                confidence = sum(token_probs) / len(token_probs) if token_probs else 0.0
                frame_idx = batch_frame_numbers[i]
                
                # Get timestamp info from the dataframe
                row = timestamps_df[timestamps_df["inference_frame_number"] == frame_idx]
                if row.empty:
                    # Fallback if frame not in CSV (should not happen in normal flow)
                    orig_frame_num = None
                    orig_timestamp = round(frame_idx / target_fps, 2)
                else:
                    orig_frame_num = int(row["original_frame_number"].values[0])
                    orig_timestamp = float(row["original_timestamp_sec"].values[0])

                pred_label = "yes" if "yes" in answer else "no"

                results.append({
                    "inference_frame_number": frame_idx,
                    "original_frame_number": orig_frame_num,
                    "timestamp_sec": orig_timestamp,
                    "predicted_label": pred_label,
                    "confidence": confidence
                })
        
        pbar.update(len(batch_images))
        batch_images.clear()
        batch_frame_numbers.clear()

    # Main video processing loop
    frame_idx = 0
    while True:
        success, frame = cap.read()
        if not success:
            break

        frame_rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        h, w, _ = frame_rgb.shape
        # Use crop coordinates from config
        crop_start = int(w * crop_coords['start'])
        crop_end = int(w * crop_coords['end'])
        cropped = frame_rgb[:, crop_start:crop_end]
        img = Image.fromarray(cropped).resize((224, 224))

        batch_images.append(img)
        batch_frame_numbers.append(frame_idx)

        # Use batch_size from config
        if len(batch_images) >= batch_size:
            process_batch()

        frame_idx += 1

    process_batch() # Process the final batch
    pbar.close()
    cap.release()
    
    # Stop GPU polling and report stats
    polling = False
    poll_thread.join()

    elapsed = time.time() - inference_start_time
    inference_fps = total_frames / elapsed if elapsed > 0 else 0
    avg_gpu_util = sum(gpu_utils) / len(gpu_utils) if gpu_utils else 0
    max_gpu_util = max(gpu_utils) if gpu_utils else 0

    log.info(f"Inference finished in {elapsed:.1f}s. Average FPS: {inference_fps:.2f}")
    log.info(f"GPU Utilization -> Average: {avg_gpu_util:.2f}%, Peak: {max_gpu_util:.2f}%")
    return pd.DataFrame(results).sort_values("inference_frame_number")


# --- Post-Processing Functions ---
def merge_intervals(intervals, max_gap_sec: float):
    """Merges intervals that are closer than max_gap_sec."""
    if not intervals:
        return []
    intervals = sorted(intervals, key=lambda x: x["start"])
    merged = [intervals[0]]
    for current in intervals[1:]:
        prev = merged[-1]
        if current["start"] - prev["end"] <= max_gap_sec: # Use config value
            merged[-1]["end"] = max(prev["end"], current["end"])
        else:
            merged.append(current)
    return merged

def postprocess_predictions(df: pd.DataFrame, config: dict) -> list[dict]:
    """Filters, groups, and buffers raw predictions to create event intervals using config."""
    df = df.copy()
    # Use confidence_threshold from config
    df['status'] = df.apply(
        lambda r: 'yes' if r['predicted_label'] == 'yes' and r['confidence'] >= config['confidence_threshold'] else 'no',
        axis=1
    )
    yes_df = df[df['status'] == 'yes']
    timestamps = yes_df['timestamp_sec'].tolist()
    timestamps.sort()

    groups = []
    if timestamps:
        current_group = [timestamps[0]]
        for ts in timestamps[1:]:
            # Use grouping_threshold_sec from config
            if ts - current_group[-1] <= config['grouping_threshold_sec']:
                current_group.append(ts)
            else:
                groups.append(current_group)
                current_group = [ts]
        groups.append(current_group)

    predicted_intervals = []
    for group in groups:
        # Use buffer values from config
        start = max(0, group[0] - config['buffer_start_sec'])
        end = group[-1] + config['buffer_end_sec']
        predicted_intervals.append({"start": round(start, 2), "end": round(end, 2)})

    # Use merge_gap_sec from config
    return merge_intervals(predicted_intervals, max_gap_sec=config['merge_gap_sec'])


# --- Main Orchestrator for this Stage ---
def run_inference(
    downsampled_video_path: Path,
    timestamps_csv_path: Path,
    output_dir: Path,
    prompt: str,
    inference_config: dict,
    post_proc_config: dict,
    target_fps: int,
) -> Path:
    """Orchestrates the inference and post-processing stage using config parameters."""
    # Pass model_id from config
    model, processor = load_model(model_id=inference_config['model_id'])
    
    # More robust way to get the original video stem
    video_stem = downsampled_video_path.stem.rsplit('_', 1)[0]

    timestamps_df = pd.read_csv(timestamps_csv_path)
    
    raw_predictions_df = _run_inference_on_video(
        model, processor, downsampled_video_path, timestamps_df, prompt,
        batch_size=inference_config['batch_size'],
        crop_coords={
            'start': inference_config['crop_width_start'],
            'end': inference_config['crop_width_end']
        },
        max_new_tokens=inference_config.get('max_new_tokens', 5), # Use get for safe access
        target_fps=target_fps
    )

    log.info(f"Post-processing predictions with threshold {post_proc_config['confidence_threshold']}...")
    post_start = time.time()
    # Pass the entire post_proc_config dictionary
    predicted_intervals = postprocess_predictions(raw_predictions_df, post_proc_config)
    log.info(f"Post-processing finished in {time.time() - post_start:.2f}s")


    intervals_csv_path = output_dir / f"{video_stem}_predicted_intervals.csv"
    pd.DataFrame(predicted_intervals).to_csv(intervals_csv_path, index=False)
    log.info(f"Saved {len(predicted_intervals)} predicted intervals to: {intervals_csv_path}")

    return intervals_csv_path
