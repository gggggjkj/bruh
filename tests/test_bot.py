import unittest
from types import MethodType, SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, create_autospec, patch

import discord

from core import Context, clean_mention, directly_mentions, split_reply
from bot import APIError, ChatBot, ask_deepseek


class CoreTests(unittest.TestCase):
    def test_direct_mentions_only(self):
        for text in ("<@123> 嗨", "<@!123> 嗨"):
            self.assertTrue(directly_mentions(text, 123))
            self.assertEqual(clean_mention(text, 123), "嗨")
        for text in ("@everyone", "<@&123>", "<@1234>", "Bot你好"):
            self.assertFalse(directly_mentions(text, 123))

    def test_channel_isolation_and_bounds(self):
        context = Context(messages=2, channels=2)
        context.append(1, "user", "old")
        context.append(1, "user", "second")
        context.append(1, "assistant", "third")
        context.append(2, "user", "separate")
        self.assertEqual([x["content"] for x in context.snapshot(1)], ["second", "third"])
        self.assertEqual(context.snapshot(2)[0]["content"], "separate")
        snapshot = context.snapshot(1)
        snapshot[0]["content"] = "changed"
        self.assertEqual(context.snapshot(1)[0]["content"], "second")
        context.append(3, "user", "x" * 4000)
        self.assertEqual(context.snapshot(1), [])
        self.assertEqual(len(context.snapshot(3)[0]["content"]), 3000)

    def test_managed_role_mentions(self):
        self.assertTrue(directly_mentions("<@&456> hi", 123, {456}))
        self.assertEqual(clean_mention("<@&456> hi", 123, {456}), "hi")
        self.assertFalse(directly_mentions("<@&789> hi", 123, {456}))
        self.assertFalse(directly_mentions("@everyone", 123, {456}))

    def test_discord_utf16_limit(self):
        original = "你好😀\n" * 1800
        parts = split_reply(original)
        self.assertEqual("".join(parts), original)
        self.assertTrue(all(len(p.encode("utf-16-le")) // 2 <= 1900 for p in parts))


class APITests(unittest.IsolatedAsyncioTestCase):
    async def test_send_matches_real_discord_signature(self):
        bot = ChatBot("fake", "test", set(), 20, 5, 800, "persona")
        channel = create_autospec(discord.TextChannel, instance=True)
        message = SimpleNamespace(channel=channel)
        message.reply = MethodType(discord.Message.reply, message)
        message.to_reference = lambda **kwargs: discord.MessageReference(
            message_id=42, channel_id=10, guild_id=1, **kwargs)
        text = "😀" * 1500
        await bot.send_text(message, text)
        calls = channel.send.await_args_list
        self.assertEqual(len(calls), 2)
        self.assertEqual("".join(call.args[0] for call in calls), text)
        self.assertFalse(calls[0].kwargs["reference"].fail_if_not_exists)
        self.assertFalse(calls[0].kwargs["mention_author"])
        self.assertEqual(calls[1].kwargs, {})
        await bot.close()

    def session_for(self, status, payload):
        response = SimpleNamespace(status=status, json=AsyncMock(return_value=payload))
        manager = MagicMock()
        manager.__aenter__ = AsyncMock(return_value=response)
        manager.__aexit__ = AsyncMock(return_value=False)
        return SimpleNamespace(post=MagicMock(return_value=manager))

    async def test_completion_payload_and_content(self):
        session = self.session_for(200, {"choices": [{"message": {"content": " 嗨！ "}}]})
        messages = [{"role": "user", "content": "hello"}]
        reply = await ask_deepseek(session, "fake-key", "test-model", messages, 800)
        self.assertEqual(reply, "嗨！")
        payload = session.post.call_args.kwargs["json"]
        self.assertEqual(payload["messages"], messages)
        self.assertEqual(payload["thinking"], {"type": "disabled"})
        self.assertEqual(payload["model"], "test-model")

    async def test_errors_are_actionable(self):
        for status, expected in ((401, "金鑰"), (402, "餘額"), (429, "頻繁"), (500, "500")):
            with self.assertRaisesRegex(APIError, expected):
                await ask_deepseek(self.session_for(status, {}), "fake", "test", [], 800)
        with self.assertRaisesRegex(APIError, "有效文字"):
            await ask_deepseek(self.session_for(200, {"choices": []}), "fake", "test", [], 800)

    async def test_ignore_bots_dm_unmentioned_and_disallowed(self):
        bot = ChatBot("fake", "test", {10}, 20, 5, 800, "persona")
        bot._connection.user = SimpleNamespace(id=123)
        bot.send_text = AsyncMock()
        message = SimpleNamespace(guild=object(), author=SimpleNamespace(id=1, bot=False, display_name="User"),
                                  webhook_id=None, channel=SimpleNamespace(id=10),
                                  content="普通聊天", attachments=[])
        await bot.on_message(message)
        bot.send_text.assert_not_awaited()
        self.assertEqual(len(bot.context.snapshot(10)), 1)
        message.content = "<@123> hi"
        message.channel.id = 11
        await bot.on_message(message)
        self.assertEqual(bot.context.snapshot(11), [])
        message.channel.id = 10
        message.author.bot = True
        await bot.on_message(message)
        message.author.bot = False
        message.guild = None
        await bot.on_message(message)
        bot.send_text.assert_not_awaited()
        await bot.close()

    async def test_mention_reply_and_failed_request_releases_channel(self):
        bot = ChatBot("fake", "test", set(), 20, 5, 800, "persona")
        bot._connection.user = SimpleNamespace(id=123)
        bot.send_text = AsyncMock()
        bot.session = SimpleNamespace(close=AsyncMock())
        typing = MagicMock()
        typing.__aenter__ = AsyncMock()
        typing.__aexit__ = AsyncMock(return_value=False)
        message = SimpleNamespace(guild=object(), author=SimpleNamespace(id=1, bot=False, display_name="User"),
                                  webhook_id=None, channel=SimpleNamespace(id=10, typing=lambda: typing),
                                  content="<@123> 你好", attachments=[])
        with patch("bot.ask_deepseek", new_callable=AsyncMock, return_value="嗨！") as api:
            await bot.on_message(message)
            self.assertEqual(api.await_args.args[3][-1]["content"], "User: 你好")
            bot.send_text.assert_awaited_with(message, "嗨！")
            self.assertEqual(bot.context.snapshot(10)[-1]["role"], "assistant")
        self.assertNotIn(10, bot.busy)
        message.author.id = 2
        message.content = "<@&456> hi"
        message.role_mentions = [SimpleNamespace(id=456, tags=SimpleNamespace(bot_id=123))]
        with patch("bot.ask_deepseek", new_callable=AsyncMock, side_effect=APIError("餘額不足")):
            await bot.on_message(message)
            bot.send_text.assert_awaited_with(message, "餘額不足")
        self.assertNotIn(10, bot.busy)
        message.role_mentions = [SimpleNamespace(id=456, tags=SimpleNamespace(bot_id=999))]
        with patch("bot.ask_deepseek", new_callable=AsyncMock) as api:
            await bot.on_message(message)
            api.assert_not_awaited()
        await bot.close()


if __name__ == "__main__":
    unittest.main()
