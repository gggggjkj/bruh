import base64
from io import BytesIO
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from PIL import Image

from bot import ChatBot
from vision import MAX_BYTES, ImageInputError, collect_images, image_part


def picture(color="red", size=(32, 32)):
    buffer = BytesIO()
    Image.new("RGB", size, color).save(buffer, "PNG")
    return buffer.getvalue()


def attachment(data=None, **extra):
    data = data or picture()
    fields = dict(filename="test.png", content_type="image/png", size=len(data),
                  read=AsyncMock(return_value=data))
    fields.update(extra)
    return SimpleNamespace(**fields)


class ImageTests(unittest.TestCase):
    def test_image_is_resized_and_encoded(self):
        part = image_part(picture(size=(2000, 1000)))
        data = base64.b64decode(part["image_url"]["url"].split(",", 1)[1])
        with Image.open(BytesIO(data)) as image:
            self.assertEqual(image.format, "JPEG")
            self.assertEqual(image.size, (1600, 800))

    def test_corrupt_or_oversized_data_is_rejected(self):
        with self.assertRaises(ImageInputError):
            image_part(b"not an image")
        with self.assertRaisesRegex(ImageInputError, "8 MiB"):
            image_part(b"x" * (MAX_BYTES + 1))

    def test_gif_uses_first_frame(self):
        buffer = BytesIO()
        Image.new("RGB", (20, 20), "red").save(buffer, "GIF", save_all=True,
            append_images=[Image.new("RGB", (20, 20), "blue")])
        part = image_part(buffer.getvalue())
        with Image.open(BytesIO(base64.b64decode(part["image_url"]["url"].split(",", 1)[1]))) as image:
            r, g, b = image.getpixel((10, 10))
            self.assertGreater(r, b + 100)


class ImageFlowTests(unittest.IsolatedAsyncioTestCase):
    async def test_attachment_and_same_channel_reply(self):
        image = attachment()
        channel = SimpleNamespace(id=10, fetch_message=AsyncMock(return_value=SimpleNamespace(attachments=[image])))
        message = SimpleNamespace(channel=channel, attachments=[image], reference=None)
        self.assertEqual(len(await collect_images(message)), 1)
        channel.fetch_message.assert_not_awaited()
        message.attachments = []
        message.reference = SimpleNamespace(channel_id=10, message_id=42)
        self.assertEqual(len(await collect_images(message)), 1)
        channel.fetch_message.assert_awaited_once_with(42)

    async def test_other_channels_and_nonimages_not_downloaded(self):
        item = attachment(filename="file.pdf", content_type="application/pdf")
        channel = SimpleNamespace(id=10, fetch_message=AsyncMock())
        message = SimpleNamespace(channel=channel, attachments=[item], reference=None)
        self.assertEqual(await collect_images(message), [])
        item.read.assert_not_awaited()
        message.attachments = []
        message.reference = SimpleNamespace(channel_id=11, message_id=42)
        self.assertEqual(await collect_images(message), [])
        channel.fetch_message.assert_not_awaited()

    async def test_limits_checked_before_download(self):
        image = attachment(size=MAX_BYTES + 1)
        message = SimpleNamespace(attachments=[image])
        with self.assertRaises(ImageInputError):
            await collect_images(message)
        image.read.assert_not_awaited()
        message.attachments = [attachment() for _ in range(4)]
        with self.assertRaisesRegex(ImageInputError, "3 張"):
            await collect_images(message)
        for item in message.attachments:
            item.read.assert_not_awaited()

    async def test_only_mentioned_images_reach_api_and_not_cache(self):
        bot = ChatBot("fake", "test", set(), 20, 5, 800, "persona")
        bot._connection.user = SimpleNamespace(id=123)
        bot.session = SimpleNamespace(close=AsyncMock())
        bot.send_text = AsyncMock()
        typing = MagicMock()
        typing.__aenter__ = AsyncMock()
        typing.__aexit__ = AsyncMock(return_value=False)
        image = attachment()
        message = SimpleNamespace(guild=object(), author=SimpleNamespace(id=1, bot=False, display_name="User"),
            webhook_id=None, channel=SimpleNamespace(id=10, typing=lambda: typing),
            content="這張圖", attachments=[image], reference=None)
        with patch("bot.ask_deepseek", new_callable=AsyncMock, return_value="紅色圖片") as api:
            await bot.on_message(message)
            image.read.assert_not_awaited()
            api.assert_not_awaited()
            message.content = "<@123> 這張圖"
            await bot.on_message(message)
            parts = api.await_args.args[3][-1]["content"]
            self.assertEqual(parts[0]["type"], "text")
            self.assertEqual(parts[1]["type"], "image_url")
            self.assertTrue(all(isinstance(item["content"], str) for item in bot.context.snapshot(10)))
            bot.send_text.assert_awaited_with(message, "紅色圖片")
        await bot.close()


if __name__ == "__main__":
    unittest.main()
