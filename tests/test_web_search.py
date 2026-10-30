import json
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock
from bot import APIError, ask_deepseek


class SearchTests(unittest.IsolatedAsyncioTestCase):
    def session(self, responses):
        managers = []
        for status, data in responses:
            manager = MagicMock()
            manager.__aenter__ = AsyncMock(return_value=SimpleNamespace(status=status, json=AsyncMock(return_value=data)))
            manager.__aexit__ = AsyncMock(return_value=False)
            managers.append(manager)
        return SimpleNamespace(post=MagicMock(side_effect=managers))

    def tool(self, arguments=None):
        return {"choices": [{"message": {"content": None, "tool_calls": [
            {"id": "call1", "type": "function", "function": {"name": "web_search", "arguments": arguments or json.dumps({"query": "test"})}}]}}]}

    async def test_search_roundtrip_without_automatic_sources_and_no_repeat(self):
        session = self.session([(200, self.tool()), (200, {"results": [
            {"title": "Official", "url": "https://example.com", "content": "evidence"}]}),
            (200, {"choices": [{"message": {"content": "answer"}}]})])
        messages = [{"role": "user", "content": "latest?"}]
        reply = await ask_deepseek(session, "fake", "test", messages, 800)
        self.assertEqual(reply, "answer")
        self.assertIn("https://example.com", session.post.call_args_list[2].kwargs["json"]["messages"][-1]["content"])
        self.assertEqual(len(messages), 1)
        self.assertEqual(session.post.call_count, 3)
        calls = session.post.call_args_list
        self.assertEqual(calls[1].kwargs["json"]["count"], 3)
        payload = calls[2].kwargs["json"]
        self.assertNotIn("tools", payload)
        self.assertEqual(payload["messages"][-1]["tool_call_id"], "call1")

    async def test_search_error_is_visible_even_if_model_omits_it(self):
        session = self.session([(200, self.tool()), (402, {}),
            (200, {"choices": [{"message": {"content": "answer"}}]})])
        reply = await ask_deepseek(session, "fake", "test", [], 800)
        self.assertIn("搜尋失敗", reply)
        self.assertIn("402", reply)

    async def test_invalid_tool_arguments_dont_search(self):
        session = self.session([(200, self.tool("broken")),
            (200, {"choices": [{"message": {"content": "answer"}}]})])
        reply = await ask_deepseek(session, "fake", "test", [], 800)
        self.assertIn("搜尋參數無效", reply)
        self.assertEqual(session.post.call_count, 2)
