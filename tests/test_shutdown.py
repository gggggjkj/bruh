import asyncio
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

import discord
from bot import ChatBot


class ShutdownTests(unittest.IsolatedAsyncioTestCase):
    def bot(self):
        bot = ChatBot("fake", "test", set(), 20, 5, 800, "persona")
        bot.refresh_window_status = MagicMock()
        bot.is_ready = MagicMock(return_value=True)
        bot.change_presence = AsyncMock()
        bot.close = AsyncMock()
        bot.session = SimpleNamespace(close=AsyncMock())
        return bot

    async def test_sleep_presence_before_close_and_only_once(self):
        bot = self.bot()
        steps = []
        async def presence(**kwargs):
            self.assertEqual(kwargs["status"], discord.Status.idle)
            self.assertEqual(kwargs["activity"].to_dict()["state"], "已進入夢鄉")
            steps.append("presence")
        async def delay(seconds):
            self.assertEqual(seconds, 3)
            steps.append("delay")
        async def close():
            steps.append("close")
        bot.change_presence.side_effect = presence
        bot.close.side_effect = close
        with patch("bot.asyncio.sleep", side_effect=delay):
            await bot.sleep_and_close()
            await bot.sleep_and_close()
        self.assertEqual(steps, ["presence", "delay", "close"])
        self.assertTrue(bot.shutting_down)

    async def test_presence_failure_still_closes(self):
        bot = self.bot()
        bot.change_presence.side_effect = OSError("offline")
        await bot.sleep_and_close()
        bot.close.assert_awaited_once()

    async def test_offline_bot_closes_without_presence(self):
        bot = self.bot()
        bot.is_ready.return_value = False
        await bot.sleep_and_close()
        bot.change_presence.assert_not_awaited()
        bot.close.assert_awaited_once()

    async def test_shutdown_ignores_new_messages(self):
        bot = self.bot()
        bot.shutting_down = True
        await bot.on_message(None)

    async def test_inflight_answer_not_sent_after_shutdown(self):
        bot = self.bot()
        bot.send_text = AsyncMock()
        typing = MagicMock()
        typing.__aenter__ = AsyncMock()
        typing.__aexit__ = AsyncMock(return_value=False)
        message = SimpleNamespace(channel=SimpleNamespace(id=10, typing=lambda: typing),
                                  attachments=[])
        started, release = asyncio.Event(), asyncio.Event()
        async def answer(*args):
            started.set()
            await release.wait()
            return "late answer"
        with patch("bot.ask_deepseek", side_effect=answer):
            task = asyncio.create_task(bot.reply_to_request(message, "hi", [
                {"role": "user", "content": "hi"}]))
            await asyncio.wait_for(started.wait(), 2)
            bot.shutting_down = True
            release.set()
            await asyncio.wait_for(task, 2)
        bot.send_text.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()
