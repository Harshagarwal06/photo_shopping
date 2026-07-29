"""Fail-closed image upload validation shared by photo endpoints."""

from __future__ import annotations

from io import BytesIO

from fastapi import HTTPException, UploadFile
from PIL import Image, UnidentifiedImageError

ALLOWED_IMAGE_TYPES = {
    "image/heic",
    "image/heif",
    "image/jpeg",
    "image/png",
    "image/webp",
}
HEIF_BRANDS = {
    b"avif",
    b"avis",
    b"heic",
    b"heim",
    b"heis",
    b"heix",
    b"hevc",
    b"hevm",
    b"hevs",
    b"hevx",
    b"mif1",
    b"msf1",
}


def is_heif_container(image_bytes: bytes) -> bool:
    return (
        len(image_bytes) >= 16
        and image_bytes[4:8] == b"ftyp"
        and image_bytes[8:12] in HEIF_BRANDS
    )


async def read_uploaded_image(
    image: UploadFile | None,
) -> tuple[bytes | None, str]:
    if image is None:
        return None, "image/jpeg"
    image_type = (image.content_type or "").casefold()
    if image_type not in ALLOWED_IMAGE_TYPES:
        raise HTTPException(
            status_code=415,
            detail="Upload a JPG, PNG, WebP, HEIC, or HEIF image.",
        )
    image_bytes = await image.read()
    if len(image_bytes) > 12 * 1024 * 1024:
        raise HTTPException(status_code=413, detail="The image must be smaller than 12 MB.")
    if not image_bytes:
        raise HTTPException(status_code=422, detail="The uploaded image is empty.")

    unreadable = HTTPException(
        status_code=422,
        detail="The uploaded file is not a readable image.",
    )
    if image_type in {"image/heic", "image/heif"}:
        if not is_heif_container(image_bytes):
            raise unreadable
    else:
        try:
            with Image.open(BytesIO(image_bytes)) as decoded:
                decoded.verify()
        except (OSError, SyntaxError, UnidentifiedImageError) as exc:
            raise unreadable from exc
    return image_bytes, image_type
