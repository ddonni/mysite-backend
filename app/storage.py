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

# 받는 이미지 형식 → 저장할 확장자. SVG는 안에 스크립트를 넣을 수 있어서 뺌.
# 확장자는 사용자가 보낸 파일 이름이 아니라 형식에서 정함(이름은 아무거나 될 수 있음).
ALLOWED_TYPES = {
    "image/jpeg": ".jpg",
    "image/png": ".png",
    "image/webp": ".webp",
    "image/gif": ".gif",
    "image/avif": ".avif",
    "image/heic": ".heic",
    "image/heif": ".heif",
}
# 프론트(shrinkImage.js)가 올리기 전에 긴 변 1200px로 줄여서 보통 수백 KB —
# 줄이지 못한 원본(GIF 등)까지 감안해 넉넉히 잡은 상한.
MAX_UPLOAD_BYTES = 10 * 1024 * 1024

_client = None


def _get_client():
    global _client
    if _client is None:
        _client = boto3.client("s3", region_name=AWS_REGION)
    return _client


async def upload_photo(file: UploadFile) -> str:
    ext = ALLOWED_TYPES.get(file.content_type, ".jpg")
    key = f"records/{uuid.uuid4().hex}{ext}"
    body = await file.read()
    _get_client().put_object(
        Bucket=S3_BUCKET_NAME,
        Key=key,
        Body=body,
        ContentType=file.content_type,
        # 키가 매번 새 uuid라 한 번 올라간 객체는 내용이 절대 안 바뀜 —
        # 브라우저가 1년 동안 다시 묻지 않고 캐시에서 바로 쓰게 함(사진을
        # 바꾸면 새 키로 올라가니 옛 캐시가 보일 일도 없음).
        CacheControl="public, max-age=31536000, immutable",
    )
    return f"https://{S3_BUCKET_NAME}.s3.{AWS_REGION}.amazonaws.com/{key}"
