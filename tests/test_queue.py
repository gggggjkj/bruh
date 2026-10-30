import asyncio
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from bot import APIError, ChatBot


class QueueTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.bot = ChatBot("fake", "test", set(), 20, 5, 800, "persona")
        self.bot._connection.user = SimpleNamespace(id=123)
        self.bot.session = SimpleNamespace(close=AsyncMock())
        self.bot.send_text = AsyncMock()
        self.bot.refresh_window_status = MagicMock()

    async def asyncTearDown(self):
        await self.bot.close()

    def message(self, user, channel=10):
        typing = MagicMock()
        typing.__aenter__ = AsyncMock()
        typing.__aexit__ = AsyncMock(return_value=False)
        return SimpleNamespace(guild=object(), author=SimpleNamespace(
            id=user, bot=False, display_name=f"User{user}"), webhook_id=None,
            channel=SimpleNamespace(id=channel, typing=lambda: typing),
            content=f"<@123> question{user}", attachments=[])

    async def test_all_mode_replies_without_mention_and_without_cooldown(self):
        with patch("bot.read_mode", return_value="all"), patch("bot.ask_deepseek", new=AsyncMock(return_value="hello")) as api:
            for _ in range(2):
                message = self.message(1)
                message.content = "你好"
                await self.bot.on_message(message)
            self.assertEqual(api.await_count, 2)
            self.assertEqual(self.bot.send_text.await_count, 2)

    async def test_switch_back_to_mention_mode_is_immediate(self):
        with patch("bot.read_mode", side_effect=["all", "mention"]), patch("bot.ask_deepseek", new=AsyncMock(return_value="hello")) as api:
            for user in (1, 2):
                message = self.message(user)
                message.content = "你好"
                await self.bot.on_message(message)
            self.assertEqual(api.await_count, 1)

    async def test_all_mode_still_ignores_bots_dms_and_disallowed_channels(self):
        with patch("bot.read_mode", return_value="all"), patch("bot.ask_deepseek", new=AsyncMock()) as api:
            message = self.message(1)
            message.author.bot = True
            await self.bot.on_message(message)
            message = self.message(2)
            message.guild = None
            await self.bot.on_message(message)
            self.bot.allowed = {999}
            await self.bot.on_message(self.message(3))
            api.assert_not_awaited()

    async def test_fifo_each_request_keeps_its_own_question(self):
        entered, release = asyncio.Event(), asyncio.Event()
        questions = []
        async def answer(session, key, model, messages, tokens):
            question = messages[-1]["content"]
            questions.append(question)
            if len(questions) == 1:
                entered.set()
                await release.wait()
            return question
        messages = [self.message(user) for user in (1, 2, 3)]
        with patch("bot.ask_deepseek", side_effect=answer):
            first = asyncio.create_task(self.bot.on_message(messages[0]))
            await asyncio.wait_for(entered.wait(), 2)
            second = asyncio.create_task(self.bot.on_message(messages[1]))
            await asyncio.sleep(0)
            third = asyncio.create_task(self.bot.on_message(messages[2]))
            await asyncio.sleep(0)
            self.assertEqual(self.bot.pending[10], 3)
            self.bot.send_text.assert_not_awaited()
            release.set()
            await asyncio.wait_for(asyncio.gather(first, second, third), 3)
        self.assertEqual(questions, [f"User{i}: question{i}" for i in (1, 2, 3)])
        self.assertEqual([call.args[0] for call in self.bot.send_text.await_args_list], messages)
        self.assertEqual(self.bot.pending, {})
        self.assertEqual(self.bot.channel_locks, {})
        self.assertEqual(self.bot.busy, set())

    async def test_failed_request_does_not_block_next_person(self):
        entered, release = asyncio.Event(), asyncio.Event()
        async def answer(session, key, model, messages, tokens):
            if messages[-1]["content"].startswith("User1:"):
                entered.set()
                await release.wait()
                raise APIError("測試 API 錯誤")
            return "第二人的答案"
        first_message, second_message = self.message(1), self.message(2)
        with patch("bot.ask_deepseek", side_effect=answer):
            first = asyncio.create_task(self.bot.on_message(first_message))
            await asyncio.wait_for(entered.wait(), 2)
            second = asyncio.create_task(self.bot.on_message(second_message))
            await asyncio.sleep(0)
            release.set()
            await asyncio.wait_for(asyncio.gather(first, second), 3)
        self.assertEqual([call.args[1] for call in self.bot.send_text.await_args_list],
                         ["測試 API 錯誤", "第二人的答案"])
        self.assertEqual(self.bot.pending, {})

    async def test_separate_channel_can_reply_while_first_channel_waits(self):
        entered, release = asyncio.Event(), asyncio.Event()
        async def answer(session, key, model, messages, tokens):
            if messages[-1]["content"].startswith("User1:"):
                entered.set()
                await release.wait()
            return "answer"
        with patch("bot.ask_deepseek", side_effect=answer):
            first = asyncio.create_task(self.bot.on_message(self.message(1)))
            await asyncio.wait_for(entered.wait(), 2)
            other = self.message(2, channel=20)
            await asyncio.wait_for(self.bot.on_message(other), 2)
            self.bot.send_text.assert_awaited_once_with(other, "answer")
            release.set()
            await asyncio.wait_for(first, 2)

    async def test_full_queue_rejects_extra_without_losing_admitted_requests(self):
        self.bot.queue_limit = 2
        entered, release = asyncio.Event(), asyncio.Event()
        async def answer(*args):
            entered.set()
            await release.wait()
            return "answer"
        with patch("bot.ask_deepseek", side_effect=answer) as api:
            first = asyncio.create_task(self.bot.on_message(self.message(1)))
            await asyncio.wait_for(entered.wait(), 2)
            second = asyncio.create_task(self.bot.on_message(self.message(2)))
            await asyncio.sleep(0)
            extra = self.message(3)
            await self.bot.on_message(extra)
            self.assertEqual(self.bot.pending[10], 2)
            self.assertIn("排隊已滿", self.bot.send_text.await_args.args[1])
            release.set()
            await asyncio.wait_for(asyncio.gather(first, second), 3)
        self.assertEqual(api.await_count, 2)
        self.assertEqual(self.bot.pending, {})


if __name__ == "__main__":
    unittest.main()
