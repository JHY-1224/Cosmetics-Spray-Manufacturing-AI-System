import asyncio
from io import BytesIO
import os
import warnings

from PIL import Image, ImageOps, UnidentifiedImageError

from .sessions import fail, uid


def normalize_image(raw, settings):
    if len(raw) > settings.image_bytes:
        fail('image_too_large', '单张图片超过上传上限', 413)
    try:
        with warnings.catch_warnings():
            warnings.simplefilter('error', Image.DecompressionBombWarning)
            with Image.open(BytesIO(raw)) as im:
                if im.format not in ('PNG', 'JPEG', 'WEBP') or getattr(im, 'n_frames', 1) != 1:
                    fail('image_format', '仅支持静态 JPEG、PNG、WebP 图片', 422)
                if im.width * im.height > settings.image_pixels:
                    fail('image_pixels', '图片像素超限，请裁剪需要分析的局部后上传', 413)
                im.verify()
            with Image.open(BytesIO(raw)) as im:
                output = ImageOps.exif_transpose(im).convert('RGB')
                target = BytesIO()
                # Preserve all allowed pixels; no blanket downscale that destroys small text.
                output.save(target, format='PNG')
                data = target.getvalue()
                if len(data) > settings.image_bytes * 3:
                    fail('decoded_too_large', '解码后图片过大，请裁剪局部', 413)
                return data, output.width, output.height
    except (UnidentifiedImageError, OSError, ValueError, Image.DecompressionBombError,
            Image.DecompressionBombWarning):
        fail('invalid_image', '图片损坏或无法安全解码，请换一张图片', 422)


async def save_image(store, owner, conversation, raw):
    if store.image_slots.locked():
        fail('image_processing_busy', '图片处理繁忙，请稍后重试', 429)
    async with store.image_slots:
        return await _save_image(store, owner, conversation, raw)


async def _save_image(store, owner, conversation, raw):
    store.own('conversations', conversation, owner)
    if len(store.all('SELECT id FROM images WHERE owner=?', (owner,))) >= 30:
        fail('upload_quota', '本访客图片数量已达上限，请清理对话', 429)
    data, width, height = await asyncio.wait_for(
        asyncio.to_thread(normalize_image, raw, store.settings), timeout=10)
    if len(store.all('SELECT id FROM images WHERE owner=?', (owner,))) >= 30:
        fail('upload_quota', '本访客图片数量已达上限，请清理对话', 429)
    total = store.one('SELECT coalesce(sum(bytes),0) AS total FROM images')['total']
    if total + len(data) > store.settings.storage_bytes:
        fail('storage_full', '临时图片存储已满，请稍后再试', 507)
    ident = uid()
    path = store.root / 'images' / (ident + '.png')
    try:
        with path.open('xb') as target:
            os.chmod(path, 0o600)
            target.write(data)
            target.flush()
            os.fsync(target.fileno())
        import time
        store.execute('INSERT INTO images VALUES (?,?,?,?,?,?,?)',
                      (ident, owner, conversation, time.time() + store.settings.ttl_seconds,
                       len(data), width, height))
    except BaseException:
        path.unlink(missing_ok=True)
        raise
    return {'id': ident, 'width': width, 'height': height, 'bytes': len(data)}
