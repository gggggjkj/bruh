"""Bounded, channel-local conversation context and Discord message splitting."""
import re
from collections import OrderedDict, deque


def directly_mentions(content: str, bot_id: int, role_ids=()) -> bool:
    return bool(re.search(rf"<@!?{bot_id}>", content)) or any(
        f"<@&{role_id}>" in content for role_id in role_ids)


def clean_mention(content: str, bot_id: int, role_ids=()) -> str:
    content = re.sub(rf"<@!?{bot_id}>", "", content)
    for role_id in role_ids:
        content = content.replace(f"<@&{role_id}>", "")
    return content.strip()


def split_reply(text: str, limit: int = 1900) -> list[str]:
    # Discord counts UTF-16 code units; astral emoji take two units.
    chunks, current, units = [], [], 0
    for char in text:
        width = 2 if ord(char) > 0xFFFF else 1
        if units + width > limit:
            chunks.append("".join(current))
            current, units = [], 0
        current.append(char)
        units += width
    if current:
        chunks.append("".join(current))
    return chunks


class Context:
    def __init__(self, messages: int = 20, channels: int = 100):
        self.messages = messages
        self.channels = channels
        self.data = OrderedDict()

    def append(self, channel_id: int, role: str, content: str):
        if channel_id not in self.data:
            self.data[channel_id] = deque(maxlen=self.messages)
        self.data.move_to_end(channel_id)
        self.data[channel_id].append({"role": role, "content": content[:3000]})
        while len(self.data) > self.channels:
            self.data.popitem(last=False)

    def snapshot(self, channel_id: int) -> list[dict]:
        return [dict(item) for item in self.data.get(channel_id, ())]
