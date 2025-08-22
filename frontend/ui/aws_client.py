"""Lightweight AWS helpers for bucket discovery and S3 clients.
Avoids any infrastructure mutations.
"""
from __future__ import annotations

import os
from typing import Optional

import boto3
from botocore.config import Config
from botocore.exceptions import ClientError

from ui.config import STACK_NAME


def get_boto3_session(region: Optional[str] = None) -> boto3.session.Session:
    if region:
        return boto3.Session(region_name=region)
    return boto3.Session()


def get_s3_client(region: Optional[str] = None):
    session = get_boto3_session(region)
    # Slightly conservative retries
    cfg = Config(retries={"max_attempts": 5, "mode": "standard"})
    return session.client("s3", config=cfg)


def discover_bucket_from_stack(region: Optional[str], stack_name: Optional[str] = None) -> Optional[str]:
    """Try to discover bucket name from CloudFormation stack outputs.
    Returns bucket or None if not resolvable.
    """
    try:
        session = get_boto3_session(region)
        cfn = session.client("cloudformation")
        resp = cfn.describe_stacks(StackName=stack_name or STACK_NAME)
        for stack in resp.get("Stacks", []):
            for out in stack.get("Outputs", []):
                # Heuristic: look for key containing 'BucketName' or similar
                if out.get("OutputKey", "").lower().endswith("bucketname"):
                    return out.get("OutputValue")
                if "bucket" in out.get("OutputKey", "").lower():
                    return out.get("OutputValue")
    except ClientError:
        return None
    except Exception:
        return None
    return None


def object_exists(s3, bucket: str, key: str) -> bool:
    try:
        s3.head_object(Bucket=bucket, Key=key)
        return True
    except ClientError as e:
        code = e.response.get("ResponseMetadata", {}).get("HTTPStatusCode")
        if code == 404:
            return False
        # For 403 or others, rethrow and let caller handle
        raise
