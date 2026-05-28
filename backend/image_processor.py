import os
import re
import hashlib
import asyncio
from pathlib import Path
from typing import Optional, Tuple
from PIL import Image, ImageFilter
from backend.config import settings
from backend.logger import app_logger

Image.MAX_IMAGE_PIXELS = 50_000_000

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

# Bandcamp art: https://f4.bcbits.com/img/a{ID}_{size}.jpg
# Size codes downscale the source; _0 is the artist's full original upload.
_BCBITS_ART_RE = re.compile(r"(https?://f4\.bcbits\.com/img/a\d+)_\d+(\.\w+)$")


def upgrade_thumbnail_url(url: str) -> str:
    if not url:
        return url
    m = _BCBITS_ART_RE.match(url)
    if m:
        return f"{m.group(1)}_0{m.group(2)}"
    return url


def parse_ratio(ratio_str: str) -> Tuple[int, int]:
    if ':' in ratio_str:
        parts = ratio_str.split(':')
        if len(parts) == 2:
            try:
                return int(parts[0]), int(parts[1])
            except ValueError:
                pass
    return 1, 1


def get_target_dimensions(ratio: str, resolution: str) -> Tuple[int, int]:
    if resolution == 'original':
        return None, None
    # Original ratio resolves to the source image's actual size — the caller
    # has to read the processed file's dimensions to show a real W×H.
    if ratio == 'original':
        return None, None

    r_w, r_h = parse_ratio(ratio)
    base = RESOLUTION_MAP.get(resolution, (1280, 720))

    w = base[0]
    h = int(w * r_h / r_w)

    if h > base[1]:
        h = base[1]
        w = int(h * r_w / r_h)

    w = w if w % 2 == 0 else w - 1
    h = h if h % 2 == 0 else h - 1

    return w, h


def get_cache_key(image_path: str, ratio: str, resolution: str) -> str:
    try:
        h = hashlib.md5()
        h.update(ratio.encode())
        h.update(resolution.encode())
        with open(image_path, 'rb') as f:
            h.update(f.read(65536))
        return h.hexdigest()
    except Exception:
        key = f"{image_path}|{ratio}|{resolution}"
        return hashlib.md5(key.encode()).hexdigest()


def _prune_cover_cache():
    """Remove oldest cached covers when total size exceeds COVER_CACHE_MAX_MB."""
    try:
        max_bytes = settings.COVER_CACHE_MAX_MB * 1024 * 1024
        files = [p for p in COVER_CACHE_DIR.glob('*.jpg') if p.is_file()]
        if not files:
            return
        total = sum(p.stat().st_size for p in files)
        if total <= max_bytes:
            return
        files.sort(key=lambda p: p.stat().st_mtime)
        while total > max_bytes and files:
            oldest = files.pop(0)
            size = oldest.stat().st_size
            oldest.unlink(missing_ok=True)
            total -= size
            app_logger.debug(f"Cover cache pruned: {oldest.name}")
    except Exception as e:
        app_logger.warning(f"Cover cache prune error: {e}")


async def process_cover_image(
    image_path: str,
    ratio: str = "1:1",
    resolution: str = "medium",
    use_cache: bool = True
) -> Optional[str]:
    # Validate ratio/resolution against known values so unknown keys don't crash
    if ratio not in RATIO_MAP and ratio != 'original':
        app_logger.warning(f"Unknown cover ratio {ratio!r}, falling back to 1:1")
        ratio = '1:1'
    if resolution not in RESOLUTION_MAP and resolution != 'original':
        app_logger.warning(f"Unknown cover resolution {resolution!r}, falling back to medium")
        resolution = 'medium'

    COVER_CACHE_DIR.mkdir(parents=True, exist_ok=True)

    # No-op fast path: original ratio AND original resolution means
    # "use the source as-is", so nothing to render. A fixed ratio with
    # resolution='original' still needs processing — the ratio crop/pad
    # is applied at the source's native size in _process_image_sync.
    if resolution == 'original' and ratio == 'original':
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
        # Prune old entries after adding a new one
        await loop.run_in_executor(None, _prune_cover_cache)
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
    with Image.open(image_path) as img:
        if img.width * img.height > 25_000_000:
            img.thumbnail((4096, 4096), Image.LANCZOS)

        orig_w, orig_h = img.size
        img = img.convert('RGB')

        if ratio == 'original':
            # Source aspect is preserved. Pick output dims so the chosen
            # resolution is always honored:
            #  - source larger than the box on any axis -> downscale to fit
            #    (no padding; output may be smaller than the box).
            #  - source smaller-or-equal on both axes -> upscale to fit and
            #    fill any aspect gap with blurry padding, so output matches
            #    the requested resolution exactly. Same blurry-pixel
            #    behavior as the fixed-ratio paths.
            base_w, base_h = RESOLUTION_MAP.get(resolution, (1280, 720))
            base_w = max(2, base_w - (base_w % 2))
            base_h = max(2, base_h - (base_h % 2))
            fit_scale = min(base_w / orig_w, base_h / orig_h)
            if fit_scale < 1.0:
                new_w = max(2, int(orig_w * fit_scale))
                new_h = max(2, int(orig_h * fit_scale))
                new_w -= new_w % 2
                new_h -= new_h % 2
                result = img.resize((new_w, new_h), Image.LANCZOS)
            elif orig_w == base_w and orig_h == base_h:
                return image_path
            elif settings.BLURRY_PADDING_ENABLED:
                result = _apply_blurry_padding(img, base_w, base_h)
            else:
                return image_path
            result.save(output_path, 'JPEG', quality=92, optimize=True)
            app_logger.debug(f"Cover processed (original ratio): {output_path}")
            return output_path

        orig_ratio = orig_w / orig_h
        r_w, r_h = parse_ratio(ratio)

        if resolution == 'original':
            # Largest crop matching the requested ratio that fits inside
            # the source — never upscales beyond the source's dimensions.
            target_w = min(orig_w, int(orig_h * r_w / r_h))
            target_h = min(orig_h, int(orig_w * r_h / r_w))
            target_w = max(2, target_w - (target_w % 2))
            target_h = max(2, target_h - (target_h % 2))
        else:
            target_w, target_h = get_target_dimensions(ratio, resolution)
            if target_w is None:
                return image_path

        target_ratio = r_w / r_h

        ratio_diff = abs(orig_ratio - target_ratio) / target_ratio

        if ratio_diff < 0.01:
            result = img.resize((target_w, target_h), Image.LANCZOS)
        elif settings.BLURRY_PADDING_ENABLED:
            result = _apply_blurry_padding(img, target_w, target_h)
        else:
            result = _center_crop(img, target_w, target_h)

        result.save(output_path, 'JPEG', quality=92, optimize=True)
        app_logger.debug(f"Cover processed: {output_path} ({target_w}x{target_h})")
        return output_path


def _apply_blurry_padding(img, target_w: int, target_h: int):
    bg = img.resize((target_w, target_h), Image.LANCZOS)
    bg = bg.filter(ImageFilter.GaussianBlur(radius=settings.BLURRY_PADDING_BLUR_RADIUS))

    img_ratio = img.width / img.height
    target_ratio = target_w / target_h

    if img_ratio > target_ratio:
        new_w = target_w
        new_h = int(target_w / img_ratio)
    else:
        new_h = target_h
        new_w = int(target_h * img_ratio)

    new_w = new_w if new_w % 2 == 0 else new_w - 1
    new_h = new_h if new_h % 2 == 0 else new_h - 1

    sharp = img.resize((new_w, new_h), Image.LANCZOS)

    x = (target_w - new_w) // 2
    y = (target_h - new_h) // 2

    result = bg.copy()
    result.paste(sharp, (x, y))
    return result


def _center_crop(img, target_w: int, target_h: int):
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
    import aiohttp

    url = upgrade_thumbnail_url(url)

    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url, timeout=aiohttp.ClientTimeout(total=30)) as resp:
                if resp.status == 200:
                    content_type = resp.headers.get('Content-Type', '').split(';')[0].strip()
                    if content_type and not content_type.startswith('image/'):
                        app_logger.warning(
                            f"Cover download returned non-image content-type "
                            f"{content_type!r} for {url}"
                        )
                        return None
                    with open(dest_path, 'wb') as f:
                        async for chunk in resp.content.iter_chunked(8192):
                            f.write(chunk)
                    return dest_path
    except Exception as e:
        app_logger.error(f"Cover download error: {e}")
    return None
