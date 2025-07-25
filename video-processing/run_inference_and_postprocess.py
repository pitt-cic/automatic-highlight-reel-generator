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

# === CONFIG ===
MODEL_ID = "google/paligemma2-3b-mix-224"
DEFAULT_FPS = 4
DEFAULT_THRESHOLD = 0.845
BATCH_SIZE = 16

log = logging.getLogger(__name__)

# === ENV SETUP ===
os.environ["TORCHDYNAMO_DISABLE"] = "1"
torch.cuda.empty_cache()
device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
log.info(f"Using device: {device}")

# === Load model and processor ===
def load_model():
    """Loads the PaliGemma model and processor."""
    log.info(f"Loading model '{MODEL_ID}' to device '{device}'...")
    processor = AutoProcessor.from_pretrained(MODEL_ID)
    model = PaliGemmaForConditionalGeneration.from_pretrained(
        MODEL_ID,
        torch_dtype=torch.float16,
        low_cpu_mem_usage=True,
    ).to(device)
    model.eval()
    log.info("Model loaded and ready.")
    return model, processor

# === GPU UTIL POLLING ===
polling = False
gpu_utils = []
def poll_gpu_utilization(interval=0.5):
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
            gpu_utils.append(0)
        time.sleep(interval)

# === Inference ===
def _run_inference_on_video(model, processor, video_path: Path, timestamps_df: pd.DataFrame, prompt: str) -> pd.DataFrame:
    """Helper function to run the core inference loop on a video file."""
    start_time = time.time()
    cap = cv2.VideoCapture(str(video_path))
    assert cap.isOpened(), f"Cannot open video: {video_path}"
    total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    log.info(f"Total frames to process: {total_frames}")

    if len(timestamps_df) != total_frames:
        log.warning(f"Timestamps CSV frame count {len(timestamps_df)} != video frame count {total_frames}")
    else:
        log.info(f"Timestamps CSV loaded with {len(timestamps_df)} rows")

    # GPU polling can be added back if detailed metrics are needed
    # global polling
    # polling = True
    # gpu_utils.clear()
    # poll_thread = threading.Thread(target=poll_gpu_utilization)
    # poll_thread.start()

    results = []
    batch_images = []
    batch_frame_numbers = []

    def process_batch():
        nonlocal batch_images, batch_frame_numbers
        if not batch_images:
            return

        with torch.no_grad():
            inputs = processor(images=batch_images, text=[prompt] * len(batch_images), return_tensors="pt", padding=True).to(device)
            outputs = model.generate(
                **inputs,
                max_new_tokens=5,
                output_scores=True,
                return_dict_in_generate=True,
            )

            answers = processor.batch_decode(outputs.sequences, skip_special_tokens=True)
            scores = outputs.scores
            generated_tokens = outputs.sequences

            for i in range(len(batch_images)):
                answer = answers[i].lower().strip().split("\n")[-1]
                token_probs = []
                for step_logits, token_id_tensor in zip(scores, generated_tokens[i, -len(scores):]):
                    token_id = token_id_tensor.item()
                    probs = F.softmax(step_logits, dim=-1)
                    token_prob = probs[i, token_id].item()
                    token_probs.append(token_prob)

                confidence = sum(token_probs) / len(token_probs) if token_probs else 0.0
                frame_idx = batch_frame_numbers[i]
                
                # Lookup original frame number and timestamp from CSV by inference_frame_number
                row = timestamps_df[timestamps_df["inference_frame_number"] == frame_idx]
                if row.empty:
                    orig_frame_num = None
                    orig_timestamp = round(frame_idx / DEFAULT_FPS, 2)
                else:
                    orig_frame_num = int(row["original_frame_number"].values[0])
                    orig_timestamp = float(row["original_timestamp_sec"].values[0])

                pred_label = "yes" if answer == "yes" else "no"

                results.append({
                    "inference_frame_number": frame_idx,
                    "original_frame_number": orig_frame_num,
                    "timestamp_sec": orig_timestamp,
                    "predicted_label": pred_label,
                    "confidence": confidence
                })

        log.info(f"Processed batch of {len(batch_images)} frames. Total processed: {results[-1]['inference_frame_number'] + 1}/{total_frames}")
        batch_images.clear()
        batch_frame_numbers.clear()

    frame_idx = 0
    while True:
        success, frame = cap.read()
        if not success:
            break

        frame_rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        h, w, _ = frame_rgb.shape
        # TODO: Make crop coordinates configurable via environment variables
        cropped = frame_rgb[:, int(w * 1 / 3):int(w * 3 / 4)]
        img = Image.fromarray(cropped)

        # Explicitly resize the image to the model's expected input size.
        img = img.resize((224, 224))

        batch_images.append(img)
        batch_frame_numbers.append(frame_idx)

        if len(batch_images) >= BATCH_SIZE:
            process_batch()

        frame_idx += 1

    process_batch()
    cap.release()
    # polling = False
    # poll_thread.join()

    elapsed = time.time() - start_time
    log.info(f"Inference finished in {elapsed:.1f}s")
    return pd.DataFrame(results).sort_values("inference_frame_number")

# === Postprocessing ===
def merge_intervals(intervals, max_gap=3.5):
    if not intervals:
        return []
    intervals = sorted(intervals, key=lambda x: x["start"])
    merged = [intervals[0]]
    for current in intervals[1:]:
        prev = merged[-1]
        if current["start"] - prev["end"] <= max_gap:
            merged[-1] = {
                "start": prev["start"],
                "end": max(prev["end"], current["end"])
            }
        else:
            merged.append(current)
    return merged

def postprocess_predictions(df: pd.DataFrame, threshold: float) -> list[dict]:
    """Filters, groups, and buffers raw predictions to create event intervals."""
    df = df.copy()
    df['status'] = df.apply(
        lambda r: 'yes' if r['predicted_label'] == 'yes' and r['confidence'] >= threshold else 'uncertain',
        axis=1
    )
    yes_df = df[df['status'] == 'yes']
    timestamps = yes_df['timestamp_sec'].tolist()

    # Filter isolated detections
    filtered_ts = []
    for i, ts in enumerate(timestamps):
        neighbors = [t for j, t in enumerate(timestamps) if j != i and abs(t - ts) <= 2.5]
        if neighbors:
            filtered_ts.append(ts)

    # Group by ≤2.5s
    filtered_ts.sort()
    groups = []
    if filtered_ts:
        current = [filtered_ts[0]]
        for ts in filtered_ts[1:]:
            if ts - current[-1] <= 2.5:
                current.append(ts)
            else:
                groups.append(current)
                current = [ts]
        groups.append(current)

    # Build buffered predicted intervals
    predicted_dives = []
    for group in groups:
        start = max(0, group[0] - 1.5)
        end = group[-1] + 3
        predicted_dives.append({"start": round(start, 2), "end": round(end, 2)})

    return merge_intervals(predicted_dives, max_gap=3.5)

def run_inference(
    downsampled_video_path: Path,
    timestamps_csv_path: Path,
    output_dir: Path,
    prompt: str,
    confidence_threshold: float = DEFAULT_THRESHOLD
) -> Path:
    """
    Orchestrates the inference and post-processing stage.

    Args:
        downsampled_video_path: Path to the low-FPS video.
        timestamps_csv_path: Path to the timestamp mapping CSV.
        output_dir: Directory to save the final intervals CSV.
        prompt: The prompt to ask the VLM for each frame.
        confidence_threshold: The minimum confidence to consider a "yes" prediction.

    Returns:
        The path to the predicted intervals CSV file.
    """
    model, processor = load_model()
    video_stem = downsampled_video_path.stem.replace("_4fps", "") # Get original stem

    timestamps_df = pd.read_csv(timestamps_csv_path)
    raw_predictions_df = _run_inference_on_video(model, processor, downsampled_video_path, timestamps_df, prompt)

    log.info(f"Post-processing predictions with threshold {confidence_threshold}...")
    predicted_intervals = postprocess_predictions(raw_predictions_df, confidence_threshold)

    intervals_csv_path = output_dir / f"{video_stem}_predicted_intervals.csv"
    pd.DataFrame(predicted_intervals).to_csv(intervals_csv_path, index=False)
    log.info(f"Saved {len(predicted_intervals)} predicted intervals to: {intervals_csv_path}")

    return intervals_csv_path
