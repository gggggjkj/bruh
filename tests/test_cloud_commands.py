import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock
from bot import ChatBot

class CloudCommandTests(unittest.IsolatedAsyncioTestCase):
    async def test_local_choices_only_return_cloud_notice(self):
        bot=ChatBot("fake", "deepseek-flash", set(),20,5,800,"persona")
        try:
            command=bot.tree.get_command("生成")
            for kind in ("text", "image"):
                interaction=SimpleNamespace(response=SimpleNamespace(send_message=AsyncMock()))
                await command.callback(interaction, SimpleNamespace(value=kind), "hello")
                self.assertEqual(interaction.response.send_message.await_args.args[0], "現在為雲端板無法使用該功能")
            self.assertEqual(bot.model,"deepseek-flash")
        finally:
            await bot.close()
