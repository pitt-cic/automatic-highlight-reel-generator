import os
import subprocess
from pathlib import Path
import pandas as pd
import logging
import time
from tqdm import tqdm

log = logging.getLogger(__name__)

# -------- FUNCTIONS --------
def extract_clip(video_path, start, end, output_path, preset, crf, audio_bitrate):
    """Extracts a single clip from a video file using FFmpeg."""
    cmd = [
        "ffmpeg", "-y",
        "-ss", str(start),
        "-to", str(end),
        "-i", str(video_path),
        "-c:v", "libx264",
        "-preset", preset,
        "-crf", str(crf),
        "-c:a", "aac",
        "-b:a", audio_bitrate,
        str(output_path)
    ]
    # Hide verbose FFmpeg output for a cleaner log
    subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True)


def merge_clips(clip_paths, output_path):
    """Merges multiple video clips into a single file using FFmpeg."""
    list_file = output_path.parent / "concat_list.txt"
    with open(list_file, "w") as f:
        for clip in clip_paths:
            # Use resolve() to get absolute path for FFmpeg concat
            f.write(f"file '{clip.resolve()}'\n")

    cmd = [
        "ffmpeg", "-y",
        "-f", "concat",
        "-safe", "0",
        "-i", str(list_file),
        "-c", "copy", # Copy streams without re-encoding for speed
        str(output_path)
    ]
    subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True)
    list_file.unlink() # Clean up the temporary list file

def run_clipping(original_video_path: Path, predicted_intervals_csv_path: Path, output_dir: Path, clipping_config: dict) -> Path:
    """
    Orchestrates the video clipping and merging process.

    Args:
        original_video_path: Path to the original, high-resolution video.
        predicted_intervals_csv_path: Path to the CSV with start/end times for clips.
        output_dir: Directory to save the final merged video.

    Returns:
        The path to the final merged highlight reel.
    """
    log.info(f"Starting clipping and merging for '{original_video_path.name}'...")
    try:
        df = pd.read_csv(predicted_intervals_csv_path)
    except FileNotFoundError:
        log.warning("Predicted intervals CSV not found. Skipping clipping.")
        return None

    if df.empty:
        log.warning(f"No intervals found in CSV. Skipping clipping.")
        return None

    video_stem = original_video_path.stem
    clips_dir = output_dir / "clips"
    clips_dir.mkdir(exist_ok=True)
    
    clip_paths = []
    # Use tqdm for a clean progress bar
    for i, row in tqdm(df.iterrows(), total=len(df), desc="🎬 Extracting Clips", unit="clip"):
        start_time = float(row["start"])
        end_time = float(row["end"])
        clip_filename = f"{video_stem}_clip_{i+1:03d}.mp4"
        clip_path = clips_dir / clip_filename

        log.info(f"Extracting clip {i+1}/{len(df)}: {start_time:.2f}s to {end_time:.2f}s -> {clip_filename}")
        try:
            # Pass config values to the helper function
            extract_clip(
                original_video_path, start_time, end_time, clip_path,
                preset=clipping_config['ffmpeg_preset'],
                crf=clipping_config['crf_value'],
                audio_bitrate=clipping_config['audio_bitrate']
            )
            clip_paths.append(clip_path)
        except Exception as e:
            log.error(f"Failed to extract clip {clip_filename}: {e}")

    if clip_paths:
        merged_filename = f"{video_stem}_highlights.mp4"
        merged_path = output_dir / merged_filename

        log.info(f"Merging {len(clip_paths)} clips into {merged_filename}...")
        merge_clips(clip_paths, merged_path)
        log.info(f"Final highlight video saved to: {merged_path}")
        return merged_path
    else:
        log.warning("No clips were extracted, so no merged video was created.")
        return None
