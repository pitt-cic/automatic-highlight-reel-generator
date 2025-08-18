"""Optional: minimal CloudWatch logs peek for Lambda/ECS during processing.
We avoid heavy dependencies; best-effort fetch of latest event message.
"""
from __future__ import annotations

from typing import Optional

import boto3
from botocore.exceptions import ClientError


def latest_log_line(region: Optional[str], log_group_names: list[str]) -> Optional[str]:
	try:
		session = boto3.Session(region_name=region) if region else boto3.Session()
		logs = session.client("logs")
		for lg in log_group_names:
			try:
				resp = logs.describe_log_streams(logGroupName=lg, orderBy="LastEventTime", descending=True, limit=1)
			except ClientError:
				continue
			streams = resp.get("logStreams", [])
			if not streams:
				continue
			stream = streams[0]["logStreamName"]
			ev = logs.get_log_events(logGroupName=lg, logStreamName=stream, limit=1, startFromHead=False)
			events = ev.get("events", [])
			if events:
				return events[-1].get("message")
	except Exception:
		return None
	return None
