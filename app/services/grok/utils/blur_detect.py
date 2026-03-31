"""
Laplacian blur detection.

Computes the variance of the Laplacian (second-order derivative) of a
grayscale image.  A low variance indicates the image lacks sharp edges
and is likely blurry.

Uses only Pillow (already a project dependency) -- no NumPy/OpenCV needed.
"""

import base64
import io
import re
from typing import Optional, Tuple

from app.core.config import get_config
from app.core.logger import logger


# Laplacian 3x3 kernel: [0,1,0,1,-4,1,0,1,0]
# Pillow clips negative values so we add an offset of 128 and compensate
# when computing variance.
_LAPLACIAN_KERNEL_SIZE = (3, 3)
_LAPLACIAN_KERNEL = [0, 1, 0, 1, -4, 1, 0, 1, 0]
_LAPLACIAN_SCALE = 1
_LAPLACIAN_OFFSET = 128


def _laplacian_variance(image_bytes: bytes) -> float:
    """Return Laplacian variance for raw image bytes."""
    from PIL import Image, ImageFilter, ImageStat

    with Image.open(io.BytesIO(image_bytes)) as img:
        gray = img.convert("L")

        # Down-sample very large images to keep computation fast.
        max_dim = 1024
        if gray.width > max_dim or gray.height > max_dim:
            ratio = min(max_dim / gray.width, max_dim / gray.height)
            new_size = (int(gray.width * ratio), int(gray.height * ratio))
            gray = gray.resize(new_size, Image.LANCZOS)

        kernel = ImageFilter.Kernel(
            _LAPLACIAN_KERNEL_SIZE,
            _LAPLACIAN_KERNEL,
            scale=_LAPLACIAN_SCALE,
            offset=_LAPLACIAN_OFFSET,
        )
        filtered = gray.filter(kernel)

        stat = ImageStat.Stat(filtered)
        # The mean is shifted by _LAPLACIAN_OFFSET; variance is unaffected
        # by the constant offset so we can use it directly.
        return stat.var[0]


def _decode_blob(blob: str) -> Optional[bytes]:
    """Decode a base64 blob (with or without data-URI prefix) to raw bytes."""
    if not blob:
        return None
    data = blob
    if "," in blob and "base64" in blob.split(",", 1)[0]:
        data = blob.split(",", 1)[1]
    data = re.sub(r"\s+", "", data)
    try:
        return base64.b64decode(data, validate=True)
    except Exception:
        return None


def is_blurry_blob(blob: str) -> Tuple[bool, float]:
    """Check whether a base64-encoded image is blurry.

    Returns:
        (is_blurry, variance) -- is_blurry is True when the Laplacian
        variance falls below the configured threshold.
    """
    threshold = float(get_config("image.blur_threshold") or 0)
    if threshold <= 0:
        return False, -1.0

    raw = _decode_blob(blob)
    if not raw:
        return False, -1.0

    try:
        variance = _laplacian_variance(raw)
    except Exception as e:
        logger.debug(f"Blur detection skipped (decode error): {e}")
        return False, -1.0

    is_blurry = variance < threshold
    if is_blurry:
        logger.info(
            f"Blur detected: variance={variance:.2f}, threshold={threshold:.2f}"
        )
    else:
        logger.debug(
            f"Blur check passed: variance={variance:.2f}, threshold={threshold:.2f}"
        )
    return is_blurry, variance


def is_blurry_bytes(image_bytes: bytes) -> Tuple[bool, float]:
    """Check whether raw image bytes represent a blurry image.

    Returns:
        (is_blurry, variance)
    """
    threshold = float(get_config("image.blur_threshold") or 0)
    if threshold <= 0:
        return False, -1.0

    try:
        variance = _laplacian_variance(image_bytes)
    except Exception as e:
        logger.debug(f"Blur detection skipped (decode error): {e}")
        return False, -1.0

    is_blurry = variance < threshold
    if is_blurry:
        logger.info(
            f"Blur detected: variance={variance:.2f}, threshold={threshold:.2f}"
        )
    else:
        logger.debug(
            f"Blur check passed: variance={variance:.2f}, threshold={threshold:.2f}"
        )
    return is_blurry, variance


__all__ = ["is_blurry_blob", "is_blurry_bytes"]
