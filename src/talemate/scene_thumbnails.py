"""
Small preview images of scene covers, for lists showing many scenes at once
(the All Scenes page), so they don't have to hold every cover at full size.

Thumbnails are WebP images made to cover a THUMBNAIL_WIDTH x THUMBNAIL_HEIGHT
box (twice the size the list shows them at, for high-DPI screens) and cached
on disk; a cached thumbnail is made again when its image changes.
"""

import hashlib
import io
import os
import uuid

import structlog
from PIL import Image, ImageOps

__all__ = [
    "THUMBNAIL_WIDTH",
    "THUMBNAIL_HEIGHT",
    "THUMBNAIL_MEDIA_TYPE",
    "make_thumbnail",
    "thumbnail",
]

log = structlog.get_logger("talemate.scene_thumbnails")

THUMBNAIL_WIDTH = 320
THUMBNAIL_HEIGHT = 440
# a very wide or tall image is still kept to a reasonable size
THUMBNAIL_MAX_SIDE = 2 * THUMBNAIL_HEIGHT
THUMBNAIL_QUALITY = 80
THUMBNAIL_MEDIA_TYPE = "image/webp"

# change when thumbnails are made differently, so cached ones are made again
VERSION = 1


def _thumbnail_size(width: int, height: int) -> tuple[int, int]:
    """
    The size the image is scaled to: covering the thumbnail box (as the list
    crops it), never larger than the image, its long side capped.
    """
    scale = min(1.0, max(THUMBNAIL_WIDTH / width, THUMBNAIL_HEIGHT / height))
    scale = min(scale, THUMBNAIL_MAX_SIDE / max(width, height))
    return max(1, round(width * scale)), max(1, round(height * scale))


def make_thumbnail(image_path: str) -> bytes:
    """
    The thumbnail of the image at image_path, as WebP bytes (the first frame
    of an animated image).
    """
    with Image.open(image_path) as image:
        # large JPEGs decode at a reduced size, which is much faster
        image.draft("RGB", (THUMBNAIL_WIDTH, THUMBNAIL_HEIGHT))
        image = ImageOps.exif_transpose(image)

        if image.mode in ("RGBA", "LA") or (
            image.mode == "P" and "transparency" in image.info
        ):
            image = image.convert("RGBA")
        elif image.mode != "RGB":
            image = image.convert("RGB")

        size = _thumbnail_size(*image.size)
        if size != image.size:
            image = image.resize(size, Image.Resampling.LANCZOS)

        buffer = io.BytesIO()
        image.save(buffer, "WEBP", quality=THUMBNAIL_QUALITY, method=4)
        return buffer.getvalue()


def _cache_path(image_path: str, cache_dir: str) -> str:
    key = (
        f"{os.path.realpath(image_path)}|{THUMBNAIL_WIDTH}x{THUMBNAIL_HEIGHT}|{VERSION}"
    )
    name = hashlib.sha1(key.encode("utf-8")).hexdigest()
    return os.path.join(cache_dir, f"{name}.webp")


def thumbnail(image_path: str, cache_dir: str | None = None) -> bytes:
    """
    The thumbnail of the image at image_path, from the cache in cache_dir when
    it is there and newer than the image, otherwise made (and cached).
    """
    if not cache_dir:
        return make_thumbnail(image_path)

    cached = _cache_path(image_path, cache_dir)
    try:
        if os.path.getmtime(cached) >= os.path.getmtime(image_path):
            with open(cached, "rb") as f:
                return f.read()
    except OSError:
        pass

    data = make_thumbnail(image_path)

    try:
        os.makedirs(cache_dir, exist_ok=True)
        partial = f"{cached}.{uuid.uuid4().hex}.part"
        with open(partial, "wb") as f:
            f.write(data)
        os.replace(partial, cached)
    except OSError as e:
        # still usable, just not cached
        log.warning("thumbnail cache", path=cached, error=str(e))

    return data
