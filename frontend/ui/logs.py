"""CloudWatch Logs helpers for pipeline progress.
Best-effort parsing of Lambda and ECS logs to derive user-friendly stage updates.
"""
from __future__ import annotations

import re
import time
from dataclasses import dataclass, field
from typing import Optional, Sequence

import boto3
from botocore.exceptions import ClientError


def latest_log_line(region: Optional[str], log_group_names: list[str]) -> Optional[str]:
	"""Return the last log line across the provided groups (best-effort)."""
	try:
		session = boto3.Session(region_name=region) if region else boto3.Session()
		logs = session.client("logs")
		for lg in log_group_names:
			try:
				resp = logs.describe_log_streams(
					logGroupName=lg, orderBy="LastEventTime", descending=True, limit=1
				)
			except ClientError:
				continue
			streams = resp.get("logStreams", [])
			if not streams:
				continue
			stream = streams[0]["logStreamName"]
			ev = logs.get_log_events(
				logGroupName=lg, logStreamName=stream, limit=1, startFromHead=False
			)
			events = ev.get("events", [])
			if events:
				return events[-1].get("message")
	except Exception:
		return None
	return None


def _get_latest_stream_events(
    logs,
    log_group: str,
    limit: int = 200,
    start_time_ms: Optional[int] = None,
    debug_info: Optional[list[str]] = None,
) -> list[dict]:
    """Fetch recent events from the most recent stream within a group (best-effort)."""
    if debug_info is None:
        debug_info = []
    try:
        debug_info.append(f"Describing streams for {log_group}...")
        resp = logs.describe_log_streams(
            logGroupName=log_group, orderBy="LastEventTime", descending=True, limit=1
        )
    except ClientError as e:
        debug_info.append(f"Error describing streams for {log_group}: {e}")
        return []
    streams = resp.get("logStreams", [])
    if not streams:
        debug_info.append(f"No streams found for {log_group}.")
        return []
    stream = streams[0]["logStreamName"]
    debug_info.append(f"Found stream '{stream}' for {log_group}.")
    try:
        kwargs = {
            "logGroupName": log_group,
            "logStreamName": stream,
            "limit": limit,
            "startFromHead": False,
        }
        if start_time_ms:
            kwargs["startTime"] = start_time_ms
            debug_info.append(f"Fetching events from {stream} since {start_time_ms}...")
        else:
            debug_info.append(f"Fetching events from {stream} (no start time).")

        ev = logs.get_log_events(**kwargs)
        events = ev.get("events", [])
        debug_info.append(f"Found {len(events)} events in {stream}.")
        return events
    except ClientError as e:
        debug_info.append(f"Error getting events from {stream}: {e}")
        return []


@dataclass
class PipelineStatus:
	# Discrete stages
	lambda_triggered: bool = False
	ecs_task_started: bool = False
	stage1_done: bool = False
	stage2_inference_pct: Optional[float] = None  # 0..100
	stage2_done: bool = False
	stage3_started: bool = False
	stage3_done: bool = False
	finished_success: bool = False
	# Bookkeeping
	last_message: Optional[str] = None
	debug_info: list[str] = field(default_factory=list)

	def overall_pct(self) -> float:
		"""Heuristic overall % based on known milestones.
		Weights: lambda 10, ecs 10, s1 20, s2 40, s3 15, finished 5.
		"""
		pct = 0.0
		if self.lambda_triggered:
			pct += 10
		if self.ecs_task_started:
			pct += 10
		if self.stage1_done:
			pct += 20
		if self.stage2_done:
			pct += 40
		elif self.stage2_inference_pct is not None:
			pct += 40 * max(0.0, min(100.0, self.stage2_inference_pct)) / 100.0
		if self.stage3_started:
			# Count most of Stage 3 upon start, the rest when done
			pct += 10
		if self.stage3_done:
			pct += 5
		if self.finished_success:
			pct = 100.0
		return max(0.0, min(100.0, pct))


def _parse_lambda_events(messages: Sequence[str], needle: Optional[str]) -> tuple[bool, bool, Optional[str]]:
	"""Return (lambda_triggered, ecs_task_started, last_message)."""
	trig = False
	ecs = False
	last = None
	for m in messages:
		last = m
		mm = m.lower()
		if ("lambda triggered" in mm) or (needle and needle.lower() in mm):
			trig = True or trig
		if "started ecs task" in mm:
			# Optionally ensure it's for our file if needle available
			if not needle or (needle.lower() in mm):
				ecs = True
	return trig, ecs, last


_INF_RE = re.compile(r"Running Inference[:\s].*?(\d{1,3})%")


def _parse_ecs_events(messages: Sequence[str]) -> PipelineStatus:
	st = PipelineStatus()
	last = None
	for m in messages:
		last = m
		low = m.lower()
		if "stage 1 (downsampling) completed" in low:
			st.stage1_done = True
		if "running inference" in low:
			m2 = _INF_RE.search(m)
			if m2:
				try:
					st.stage2_inference_pct = float(m2.group(1))
				except Exception:
					pass
		if "stage 2 (inference) completed" in low:
			st.stage2_done = True
			st.stage2_inference_pct = 100.0
		if "starting clipping and merging" in low:
			st.stage3_started = True
		if ("final highlight video saved" in low) or ("stage 3 (clipping & merging) completed" in low):
			st.stage3_done = True
		if "finished successfully" in low:
			st.finished_success = True
	st.last_message = last
	return st


def get_pipeline_status(
    region: Optional[str],
    stack_name: str,
    input_key: str,
    start_time: Optional[float] = None,
) -> PipelineStatus:
    """Peek CloudWatch logs to infer pipeline stage for a given input key.

    This is best-effort and relies on the latest stream in each log group.
    """
    session = boto3.Session(region_name=region) if region else boto3.Session()
    logs = session.client("logs")
    lambda_group = f"/aws/lambda/{stack_name}-VideoTriggerLambda"
    ecs_group = f"/ecs/video-processor-{stack_name}"
    start_time_ms = int(start_time * 1000) if start_time else None
    st = PipelineStatus()

    base = input_key.split("/")[-1]
    # Lambda
    lambda_events = _get_latest_stream_events(
        logs, lambda_group, limit=100, start_time_ms=start_time_ms, debug_info=st.debug_info
    )
    lambda_msgs = [e.get("message", "") for e in lambda_events]
    trig, ecs_started, last1 = _parse_lambda_events(lambda_msgs, base)

    # ECS
    ecs_events = _get_latest_stream_events(
        logs, ecs_group, limit=200, start_time_ms=start_time_ms, debug_info=st.debug_info
    )
    ecs_msgs = [e.get("message", "") for e in ecs_events]
    st = _parse_ecs_events(ecs_msgs)
    st.lambda_triggered = trig
    st.ecs_task_started = (
        ecs_started or st.stage1_done or st.stage2_done or st.stage3_started
    )
    st.last_message = st.last_message or last1
    return st

