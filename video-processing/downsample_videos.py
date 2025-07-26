import subprocess
from pathlib import Path
import pandas as pd
import cv2
import logging

log = logging.getLogger(__name__)

# -------- FPS DETECTION --------
def get_original_fps(video_path: Path) -> float:
    """Detects the original frames per second (FPS) of a video file."""
    cmd = [
        "ffprobe", "-v", "error",
        "-select_streams", "v:0", "-show_entries", "stream=avg_frame_rate",
        "-of", "default=nokey=1:noprint_wrappers=1", str(video_path)
    ]
    output = subprocess.check_output(cmd).decode().strip()
    num, denom = map(int, output.split('/'))
    fps = num / denom
    log.info(f"Detected original FPS: {fps:.3f}")
    return fps

# -------- TIMESTAMP MAPPING --------
def generate_timestamp_mapping(downsampled_video_path: Path, orig_fps: float, target_fps: float) -> pd.DataFrame:
    """Generates a DataFrame mapping frames in the downsampled video to original timestamps."""
    cap_down = cv2.VideoCapture(str(downsampled_video_path))
    downsampled_total_frames = int(cap_down.get(cv2.CAP_PROP_FRAME_COUNT))
    cap_down.release()

    stride = round(orig_fps / target_fps)
    rows = []
    for i in range(downsampled_total_frames):
        original_frame_number = i * stride
        original_timestamp = original_frame_number / orig_fps
        rows.append({
            "inference_frame_number": i,
            "original_frame_number": original_frame_number,
            "original_timestamp_sec": round(original_timestamp, 3)
        })

    return pd.DataFrame(rows)

# -------- DOWNSAMPLING --------
def downsample_video(input_path: Path, output_path: Path, target_fps: int):
    """Downsamples a video to a target FPS using FFmpeg."""
    cmd = [
        "ffmpeg",
        "-y", "-i", str(input_path),
        "-filter:v", f"fps={target_fps}",
        "-vsync", "vfr", "-start_at_zero",
        "-c:v", "libx264", "-preset", "fast", "-crf", "28", "-an",
        str(output_path)
    ]
    subprocess.run(cmd, check=True)

def run_downsampling(input_video_path: Path, output_dir: Path, target_fps: int = 4) -> tuple[Path, Path]:
    """
    Orchestrates the video downsampling process.

    Args:
        input_video_path: Path to the original video file.
        output_dir: Directory to save the downsampled video and timestamps CSV.
        target_fps: The target frames per second for the output video.

    Returns:
        A tuple containing the path to the downsampled video and the timestamps CSV.
    """
    video_stem = input_video_path.stem
    log.info(f"Starting downsampling for '{video_stem}' to {target_fps} FPS...")

    downsampled_video_path = output_dir / f"{video_stem}_{target_fps}fps.mp4"
    downsample_video(input_video_path, downsampled_video_path, target_fps)
    log.info(f"Downsampled video saved to: {downsampled_video_path}")

    orig_fps = get_original_fps(input_video_path)
    timestamp_df = generate_timestamp_mapping(downsampled_video_path, orig_fps, target_fps)
    timestamps_csv_path = output_dir / f"{video_stem}_timestamps.csv"
    timestamp_df.to_csv(timestamps_csv_path, index=False)
    log.info(f"Timestamp mapping saved to: {timestamps_csv_path}")

    return downsampled_video_path, timestamps_csv_path
