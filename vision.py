"""Bounded image preparation for DeepSeek's multimodal Chat Completions."""
import asyncio
import base64
from io import BytesIO
from pathlib import Path
import warnings

import discord
from PIL import Image, ImageOps, UnidentifiedImageError

MAX_BYTES = 8 * 1024 * 1024
MAX_IMAGES = 3


class ImageInputError(Exception):
    pass


def is_image(attachment):
    return (getattr(attachment, "content_type", "") or "").startswith("image/") or (
        Path(getattr(attachment, "filename", "")).suffix.lower()
        in {".jpg", ".jpeg", ".png", ".gif", ".webp"})


def image_part(data):
    if len(data) > MAX_BYTES:
        raise ImageInputError("每張圖片請小於 8 MiB。")
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(BytesIO(data)) as original:
                if original.format not in {"JPEG", "PNG", "GIF", "WEBP"}:
                    raise ImageInputError("圖片格式需為 JPG、PNG、GIF 或 WebP。")
                if original.width * original.height > 20_000_000:
                    raise ImageInputError("圖片解析度過大，請縮小至 2000 萬像素以下。")
                original.seek(0)
                image = ImageOps.exif_transpose(original)
                image.thumbnail((1600, 1600))
                rgba = image.convert("RGBA")
                background = Image.new("RGB", rgba.size, "white")
                background.paste(rgba, mask=rgba.getchannel("A"))
                output = BytesIO()
                background.save(output, format="JPEG", quality=88)
    except (UnidentifiedImageError, OSError, ValueError,
            Image.DecompressionBombWarning, Image.DecompressionBombError):
        raise ImageInputError("無法讀取圖片，請重新上傳 JPG、PNG、GIF 或 WebP。") from None
    encoded = base64.b64encode(output.getvalue()).decode("ascii")
    return {"type": "image_url", "image_url": {"url": "data:image/jpeg;base64," + encoded}}


async def collect_images(message):
    attachments = list(message.attachments)
    if not attachments:
        reference = getattr(message, "reference", None)
        if (reference and reference.message_id
                and reference.channel_id == message.channel.id):
            try:
                source = await message.channel.fetch_message(reference.message_id)
            except discord.HTTPException:
                raise ImageInputError("讀不到你回覆的訊息，請直接重新附圖並 @我。") from None
            attachments = list(source.attachments)
    images = [attachment for attachment in attachments if is_image(attachment)]
    if len(images) > MAX_IMAGES:
        raise ImageInputError("一次最多分析 3 張圖片，請分次傳送。")
    parts = []
    for attachment in images:
        if attachment.size > MAX_BYTES:
            raise ImageInputError("每張圖片請小於 8 MiB。")
        try:
            data = await asyncio.wait_for(attachment.read(), timeout=25)
        except discord.HTTPException:
            raise ImageInputError("圖片無法下載，請重新附圖並 @我。") from None
        parts.append(await asyncio.to_thread(image_part, data))
    return parts
