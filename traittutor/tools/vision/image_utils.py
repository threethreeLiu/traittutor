"""Image processing utilities - URL download and format conversion."""

import base64
import logging
from urllib.parse import urlparse

import httpx

logger = logging.getLogger(__name__)

# Supported image MIME types
SUPPORTED_IMAGE_TYPES = {
    "image/jpeg": "jpeg",
    "image/jpg": "jpg",
    "image/png": "png",
    "image/gif": "gif",
    "image/webp": "webp",
}

# Maximum image size (10MB)
MAX_IMAGE_SIZE = 10 * 1024 * 1024

# Request timeout (seconds)
REQUEST_TIMEOUT = 30


class ImageError(Exception):
    """Image processing error."""

    pass


def is_valid_image_url(url: str) -> bool:
    """Check if a URL is valid for images.

    Args:
        url: URL to check

    Returns:
        True if valid URL format
    """
    try:
        parsed = urlparse(url)
        return parsed.scheme in ("http", "https") and bool(parsed.netloc)
    except Exception:
        return False


def is_base64_image(data: str) -> bool:
    """Check if data is base64 encoded image.

    Args:
        data: Data to check

    Returns:
        True if data:image/...;base64,... format
    """
    return data.startswith("data:image/") and ";base64," in data


async def fetch_image_from_url(url: str) -> tuple[bytes, str]:
    """Download image from URL.

    Args:
        url: Image URL

    Returns:
        (image bytes, MIME type)

    Raises:
        ImageError: If download fails or format unsupported
    """
    if not is_valid_image_url(url):
        raise ImageError(f"Invalid image URL: {url}")

    logger.info(f"Fetching image from URL: {url[:100]}...")

    try:
        async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT, follow_redirects=True) as client:
            response = await client.get(url)
            response.raise_for_status()

            # Check Content-Type
            content_type = response.headers.get("content-type", "").split(";")[0].strip().lower()

            # If no Content-Type, infer from URL
            if not content_type or content_type == "application/octet-stream":
                content_type = guess_image_type_from_url(url)

            if content_type not in SUPPORTED_IMAGE_TYPES:
                raise ImageError(f"Unsupported image format: {content_type}")

            # Check size
            content = response.content
            if len(content) > MAX_IMAGE_SIZE:
                raise ImageError(
                    f"Image too large: {len(content) / 1024 / 1024:.1f}MB "
                    f"(max {MAX_IMAGE_SIZE / 1024 / 1024:.0f}MB)"
                )

            logger.info(f"Image fetched successfully: {len(content)} bytes, type: {content_type}")

            return content, content_type

    except httpx.HTTPStatusError as e:
        raise ImageError(f"Failed to download image: HTTP {e.response.status_code}")
    except httpx.TimeoutException:
        raise ImageError(f"Image download timeout ({REQUEST_TIMEOUT}s)")
    except httpx.RequestError as e:
        raise ImageError(f"Failed to download image: {e!s}")


def guess_image_type_from_url(url: str) -> str:
    """Infer image type from URL.

    Args:
        url: Image URL

    Returns:
        Inferred MIME type
    """
    url_lower = url.lower()

    if ".png" in url_lower:
        return "image/png"
    elif ".jpg" in url_lower or ".jpeg" in url_lower:
        return "image/jpeg"
    elif ".gif" in url_lower:
        return "image/gif"
    elif ".webp" in url_lower:
        return "image/webp"
    else:
        # Default to JPEG
        return "image/jpeg"


def image_bytes_to_base64(content: bytes, mime_type: str) -> str:
    """Convert image bytes to base64 data URL.

    Args:
        content: Image binary data
        mime_type: MIME type

    Returns:
        data:image/...;base64,... format string
    """
    b64_data = base64.b64encode(content).decode("utf-8")
    result = f"data:{mime_type};base64,{b64_data}"

    logger.debug(f"image_bytes_to_base64: input={len(content)} bytes, output={len(b64_data)} chars")

    return result


async def url_to_base64(url: str) -> str:
    """Convert image URL to base64 data URL.

    Args:
        url: Image URL

    Returns:
        data:image/...;base64,... format string

    Raises:
        ImageError: If download or conversion fails
    """
    content, mime_type = await fetch_image_from_url(url)
    return image_bytes_to_base64(content, mime_type)


async def resolve_image_input(
    image_base64: str | None = None,
    image_url: str | None = None,
) -> str | None:
    """Resolve image input to base64 format.

    Prioritizes image_base64, falls back to downloading from image_url.

    Args:
        image_base64: Base64 format image data
        image_url: Image URL

    Returns:
        Base64 format image data, or None if no image

    Raises:
        ImageError: If URL download or conversion fails
    """
    logger.debug(
        f"resolve_image_input: base64={'yes' if image_base64 else 'no'}, url={image_url or 'none'}"
    )

    # Prefer base64
    if image_base64:
        if is_base64_image(image_base64):
            logger.debug("Using provided base64 image")
            return image_base64
        else:
            logger.error("Invalid base64 image format")
            raise ImageError("Invalid base64 image format, should be data:image/...;base64,...")

    # Try to download from URL
    if image_url:
        logger.debug("Downloading image from URL")
        result = await url_to_base64(image_url)
        logger.debug(f"Download complete, base64 length: {len(result)}")
        return result

    logger.debug("No image provided")
    return None
