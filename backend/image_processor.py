import os
import hashlib
import asyncio
from pathlib import Path
from typing import Optional, Tuple
from backend.config import settings
from backend.logger import app_logger

RESOLUTION_MAP = {
    'low': (854, 480),
    'medium': (1280, 720),
    'high': (1920, 1080),
}

RATIO_MAP = {
    '1:1': (1, 1),
    '16:9': (16, 9),
    '9:16': (9, 16),
    '4:3': (4, 3),
}

COVER_CACHE_DIR = Path(settings.UPLOAD_FOLDER) / ".cover_cache"


def parse_ratio(ratio_str: str) -> Tuple[int, int]:
    """Parse ratio string like '16:9' into (16, 9)."""
    if ':' in ratio_str:
        parts = ratio_str.split(':')
        if len(parts) == 2:
            return int(parts[0]), int(parts[1])
    return 1, 1


def get_target_dimensions(ratio: str, resolution: str) -> Tuple[int, int]:
    """Get target width/height from ratio and resolution."""
    r_w, r_h = parse_ratio(ratio)

    if resolution == 'original':
        return None, None  # Signals "no resize"

    # Determine base size from resolution
    base = RESOLUTION_MAP.get(resolution, (1280, 720))

    # Fit within base while maintaining ratio
    w = base[0]
    h = int(w * r_h / r_w)

    if h > base[1]:
        h = base[1]
        w = int(h * r_w / r_h)

    # Ensure even numbers for video encoding
    w = w if w % 2 == 0 else w - 1
    h = h if h % 2 == 0 else h - 1

    return w, h


def get_cache_key(image_path: str, ratio: str, resolution: str) -> str:
    """
    Cache key based on file content hash + settings, NOT the file path.
    Bug fix: using image_path as key meant every UUID temp path was unique,
    so the cache never hit. Content hashing ensures same image reuses cache.
    """
    import hashlib
    try:
        # Hash first 64KB of the image (fast, sufficient for uniqueness)
        h = hashlib.md5()
        h.update(ratio.encode())
        h.update(resolution.encode())
        with open(image_path, 'rb') as f:
            h.update(f.read(65536))
        return h.hexdigest()
    except Exception:
        # Fallback to path-based key if file is unreadable
        key = f"{image_path}|{ratio}|{resolution}"
        return hashlib.md5(key.encode()).hexdigest()


async def process_cover_image(
    image_path: str,
    ratio: str = "1:1",
    resolution: str = "medium",
    use_cache: bool = True
) -> Optional[str]:
    """
    Process cover image with ratio/resolution options.
    Returns path to processed image.
    """
    from PIL import Image, ImageFilter

    COVER_CACHE_DIR.mkdir(parents=True, exist_ok=True)

    # If original, return as-is
    if resolution == 'original':
        return image_path

    cache_key = get_cache_key(image_path, ratio, resolution)
    cache_path = COVER_CACHE_DIR / f"{cache_key}.jpg"

    if use_cache and cache_path.exists():
        app_logger.debug(f"Cover cache hit: {cache_key}")
        return str(cache_path)

    try:
        loop = asyncio.get_running_loop()
        result = await loop.run_in_executor(
            None,
            _process_image_sync,
            image_path, ratio, resolution, str(cache_path)
        )
        return result
    except Exception as e:
        app_logger.error(f"Image processing error: {e}")
        return image_path


def _process_image_sync(
    image_path: str,
    ratio: str,
    resolution: str,
    output_path: str
) -> str:
    from PIL import Image, ImageFilter

    with Image.open(image_path) as img:
        orig_w, orig_h = img.size
        orig_ratio = orig_w / orig_h

        target_w, target_h = get_target_dimensions(ratio, resolution)
        if target_w is None:
            return image_path

        r_w, r_h = parse_ratio(ratio)
        target_ratio = r_w / r_h

        img = img.convert('RGB')

        # Check if ratio matches
        ratio_diff = abs(orig_ratio - target_ratio) / target_ratio

        if ratio_diff < 0.01:
            # Same ratio — simple resize
            result = img.resize((target_w, target_h), Image.LANCZOS)
        elif settings.BLURRY_PADDING_ENABLED:
            # Apply blurry background padding
            result = _apply_blurry_padding(img, target_w, target_h)
        else:
            # Center crop
            result = _center_crop(img, target_w, target_h)

        result.save(output_path, 'JPEG', quality=92, optimize=True)
        app_logger.debug(f"Cover processed: {output_path} ({target_w}x{target_h})")
        return output_path


def _apply_blurry_padding(img, target_w: int, target_h: int):
    from PIL import Image, ImageFilter

    # Scale up original to fill target dimensions
    bg = img.resize((target_w, target_h), Image.LANCZOS)

    # Apply Gaussian blur
    bg = bg.filter(ImageFilter.GaussianBlur(
        radius=settings.BLURRY_PADDING_BLUR_RADIUS
    ))

    # Scale original to fit within target (maintaining aspect ratio)
    img_ratio = img.width / img.height
    target_ratio = target_w / target_h

    if img_ratio > target_ratio:
        new_w = target_w
        new_h = int(target_w / img_ratio)
    else:
        new_h = target_h
        new_w = int(target_h * img_ratio)

    # Ensure even
    new_w = new_w if new_w % 2 == 0 else new_w - 1
    new_h = new_h if new_h % 2 == 0 else new_h - 1

    sharp = img.resize((new_w, new_h), Image.LANCZOS)

    # Center paste
    x = (target_w - new_w) // 2
    y = (target_h - new_h) // 2

    result = bg.copy()
    result.paste(sharp, (x, y))
    return result


def _center_crop(img, target_w: int, target_h: int):
    from PIL import Image

    img_ratio = img.width / img.height
    target_ratio = target_w / target_h

    if img_ratio > target_ratio:
        new_h = img.height
        new_w = int(img.height * target_ratio)
    else:
        new_w = img.width
        new_h = int(img.width / target_ratio)

    left = (img.width - new_w) // 2
    top = (img.height - new_h) // 2
    cropped = img.crop((left, top, left + new_w, top + new_h))
    return cropped.resize((target_w, target_h), Image.LANCZOS)


async def download_cover_image(url: str, dest_path: str) -> Optional[str]:
    """Download cover image from URL."""
    import aiohttp

    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url, timeout=aiohttp.ClientTimeout(total=30)) as resp:
                if resp.status == 200:
                    with open(dest_path, 'wb') as f:
                        async for chunk in resp.content.iter_chunked(8192):
                            f.write(chunk)
                    return dest_path
    except Exception as e:
        app_logger.error(f"Cover download error: {e}")
    return None
