"""Photo uploads: the browser sends the file to us, we forward it to S3 and
hand back a public URL. Keeps image bytes out of the database — records
only ever store a photo_url string.
"""
import os
import uuid

import boto3
from fastapi import UploadFile

S3_BUCKET_NAME = os.getenv("S3_BUCKET_NAME")
AWS_REGION = os.getenv("AWS_REGION", "ap-northeast-2")

_client = None


def _get_client():
    global _client
    if _client is None:
        _client = boto3.client("s3", region_name=AWS_REGION)
    return _client


async def upload_photo(file: UploadFile) -> str:
    ext = os.path.splitext(file.filename or "")[1] or ".jpg"
    key = f"records/{uuid.uuid4().hex}{ext}"
    body = await file.read()
    _get_client().put_object(
        Bucket=S3_BUCKET_NAME,
        Key=key,
        Body=body,
        ContentType=file.content_type or "application/octet-stream",
    )
    return f"https://{S3_BUCKET_NAME}.s3.{AWS_REGION}.amazonaws.com/{key}"
