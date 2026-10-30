"""Local Discord mention bot backed by DeepSeek Chat Completions."""
import asyncio
import ctypes
import logging
import os
import sys
import threading
import time
from pathlib import Path

import aiohttp
import discord
from dotenv import load_dotenv

from core import Context, clean_mention, directly_mentions, split_reply
from reply_mode import read_mode
from cloud_commands import install as install_google_commands
from web_search import complete, search_instruction
from vision import ImageInputError, collect_images, is_image

ROOT = Path(__file__).resolve().parent
log = logging.getLogger("chatbot")


def set_window_status(status):
    """Show live status on the Windows console's taskbar/window title."""
    if os.name == "nt":
        ctypes.windll.kernel32.SetConsoleTitleW(f"Discord Bot | {status}")
    if os.getenv("BOT_TRAY_MODE") == "1":
        print(f"__BOT_STATUS__:{status}", flush=True)


def bounded_int(name, default, low, high):
    value = int(os.getenv(name, str(default)))
    if not low <= value <= high:
        raise ValueError(f"{name} 必須介於 {low} 到 {high}")
    return value


class APIError(Exception):
    pass


async def ask_deepseek(session, api_key, model, messages, max_tokens):
    return await complete(session, api_key, model, messages, max_tokens, APIError)


class ChatBot(discord.Client):
    def __init__(self, api_key, model, allowed, context_size, cooldown, max_tokens, persona):
        intents = discord.Intents.none()
        intents.guilds = True
        intents.guild_messages = True
        intents.message_content = True
        super().__init__(intents=intents, allowed_mentions=discord.AllowedMentions.none())
        self.api_key, self.model, self.allowed = api_key, model, allowed
        self.context = Context(context_size)
        self.cooldown, self.max_tokens, self.persona = cooldown, max_tokens, persona
        self.busy = set()
        self.channel_locks = {}
        self.pending = {}
        self.queue_limit = 20
        self.shutting_down = False
        self.last_call = {}
        self.api_slots = asyncio.Semaphore(3)
        self.tree = install_google_commands(self)

    async def setup_hook(self):
        self.session = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=90))
        try:
            await self.tree.sync()
            log.info("Discord /生成 指令同步完成。")
        except discord.HTTPException:
            log.warning("Discord 指令同步失敗，請檢查 applications.commands 安裝範圍。")
        if os.getenv("BOT_TRAY_MODE") == "1":
            def wait_for_stop():
                # The tray owns stdin. EOF also closes the bot if its tray exits.
                command = None
                for line in sys.stdin:
                    if line.strip() in {"STOP", "RESTART"}:
                        command = line.strip()
                        break
                try:
                    action = self.sleep_and_close if command == "STOP" else self.close
                    self.loop.call_soon_threadsafe(lambda: asyncio.create_task(action()))
                except RuntimeError:
                    pass
            threading.Thread(target=wait_for_stop, daemon=True).start()

    async def close(self):
        if hasattr(self, "session"):
            await self.session.close()
        await super().close()

    async def sleep_and_close(self):
        if self.shutting_down:
            return
        self.shutting_down = True
        self.refresh_window_status()
        try:
            if self.is_ready():
                try:
                    await asyncio.wait_for(self.change_presence(
                        status=discord.Status.idle,
                        activity=discord.CustomActivity(name="已進入夢鄉")), timeout=2)
                    log.info("停止應用：已設定 Discord 狀態為「已進入夢鄉」。")
                    await asyncio.sleep(3)
                except (asyncio.TimeoutError, discord.DiscordException, OSError):
                    log.warning("無法更新睡眠狀態，仍將關閉應用。")
        finally:
            await self.close()

    async def on_ready(self):
        self.refresh_window_status()
        log.info("Bot 已上線：%s。請在頻道 @它；Ctrl+C 可停止。", self.user)

    def refresh_window_status(self):
        if self.shutting_down:
            set_window_status("已進入夢鄉，正在關閉")
        elif self.pending:
            waiting = max(0, sum(self.pending.values()) - len(self.busy))
            set_window_status(f"回覆中（處理 {len(self.busy)} 個頻道，排隊 {waiting} 則）")
        elif self.is_ready():
            set_window_status(f"已上線 - {self.user}")
        else:
            set_window_status("連線中")

    async def on_disconnect(self):
        if not self.shutting_down:
            set_window_status("連線中斷，正在重新連線")

    async def on_resumed(self):
        self.refresh_window_status()

    async def send_text(self, message, text):
        for index, chunk in enumerate(split_reply(text)):
            if index == 0:
                await message.channel.send(
                    chunk,
                    reference=message.to_reference(fail_if_not_exists=False),
                    mention_author=False,
                )
            else:
                await message.channel.send(chunk)

    async def on_message(self, message):
        if self.shutting_down or not self.user or message.guild is None or message.author.bot or message.webhook_id:
            return
        channel_id = message.channel.id
        if self.allowed and channel_id not in self.allowed:
            return
        # Accept only this application's managed role, never general server roles.
        bot_role_ids = {role.id for role in getattr(message, "role_mentions", ())
                        if getattr(getattr(role, "tags", None), "bot_id", None) == self.user.id}
        content = clean_mention(message.content, self.user.id, bot_role_ids)
        if message.attachments:
            content += "\n[使用者附上圖片]" if any(is_image(a) for a in message.attachments) else "\n[附加檔案非圖片，未讀取內容]"
        if content.strip():
            self.context.append(channel_id, "user", f"{message.author.display_name}: {content}")
        reply_all = read_mode() == "all"
        if not reply_all and not directly_mentions(message.content, self.user.id, bot_role_ids):
            return
        log.info("收到回覆請求，頻道 %s，模式 %s", channel_id, "全回" if reply_all else "僅 @")
        # Capture this request before awaiting: later arrivals must not become its question.
        request_context = self.context.snapshot(channel_id)
        now = time.monotonic()
        self.last_call = {key: stamp for key, stamp in self.last_call.items()
                          if now - stamp < self.cooldown}
        key = (channel_id, message.author.id)
        try:
            if not reply_all and key in self.last_call:
                await self.send_text(message, "稍等幾秒再 @我喔。")
                return
            if self.pending.get(channel_id, 0) >= self.queue_limit or sum(self.pending.values()) >= 100:
                await self.send_text(message, "目前排隊已滿，請稍後再 @我。")
                return
        except discord.HTTPException:
            log.warning("無法傳送排隊提示，請檢查頻道權限。")
            return
        self.last_call[key] = now
        lock = self.channel_locks.setdefault(channel_id, asyncio.Lock())
        self.pending[channel_id] = self.pending.get(channel_id, 0) + 1
        self.refresh_window_status()
        try:
            # asyncio.Lock admits waiters in arrival order; hold it through sending/errors.
            async with lock:
                self.busy.add(channel_id)
                self.refresh_window_status()
                try:
                    await self.reply_to_request(message, content, request_context)
                finally:
                    self.busy.discard(channel_id)
        finally:
            self.pending[channel_id] -= 1
            if self.pending[channel_id] == 0:
                del self.pending[channel_id]
                self.channel_locks.pop(channel_id, None)
            self.refresh_window_status()

    async def reply_to_request(self, message, content, request_context):
        if self.shutting_down:
            return
        channel_id = message.channel.id
        try:
            if not content.strip() and not getattr(message, "reference", None):
                await self.send_text(message, "我在！想聊什麼？")
                return
            messages = [{"role": "system", "content": self.persona + "\n" + search_instruction() + "\n本次觸發可能來自 @ 或全回模式。請以最後一則使用者訊息為本次主題，其他訊息只有相關時才引用，不必要求對方 @你。只保留目前頻道的短期對話上下文，不會永久保存聊天或教學內容，請勿聲稱已永久記住。"}]
            messages.extend(request_context)
            async with message.channel.typing():
                async with self.api_slots:
                    parts = await collect_images(message)
                    if parts:
                        prompt = f"{message.author.display_name}: {content or '請描述這張圖片。'}"
                        current = {"role": "user", "content": [
                            {"type": "text", "text": prompt[:3000]}, *parts]}
                        if content.strip():
                            messages[-1] = current
                        else:
                            messages.append(current)
                    reply = await ask_deepseek(self.session, self.api_key, self.model,
                                               messages, self.max_tokens)
                if self.shutting_down:
                    return
                await self.send_text(message, reply)
            log.info("回覆已送出，頻道 %s", channel_id)
            self.context.append(channel_id, "assistant", reply)
        except (APIError, ImageInputError, asyncio.TimeoutError, aiohttp.ClientError) as error:
            text = str(error) if isinstance(error, (APIError, ImageInputError)) else "連線逾時或網路異常，請稍後再試。"
            log.warning("回覆失敗：%s", type(error).__name__)
            try:
                await self.send_text(message, text)
            except discord.HTTPException:
                log.warning("無法傳送錯誤提示，請檢查頻道權限。")
        except discord.HTTPException as error:
            log.warning("Discord 傳送失敗（HTTP %s），請檢查讀寫頻道與討論串權限。", error.status)


def main():
    set_window_status("啟動中")
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    load_dotenv(ROOT / ".env", override=False)
    token = os.getenv("DISCORD_BOT_TOKEN", "").strip()
    api_key = os.getenv("DEEPSEEK_API_KEY", "").strip()
    if not token or not api_key:
        set_window_status("設定未完成")
        print("請先在 .env 填入 DISCORD_BOT_TOKEN 與 DEEPSEEK_API_KEY。")
        return 1
    try:
        allowed = {int(value.strip()) for value in os.getenv("ALLOWED_CHANNEL_IDS", "").split(",") if value.strip()}
        bot = ChatBot(api_key, os.getenv("DEEPSEEK_MODEL", "deepseek-flash").strip(), allowed,
                      bounded_int("CONTEXT_MESSAGES", 20, 1, 50),
                      bounded_int("COOLDOWN_SECONDS", 5, 1, 300),
                      bounded_int("MAX_REPLY_TOKENS", 800, 50, 2000),
                      (ROOT / "persona.txt").read_text(encoding="utf-8"))
    except (ValueError, OSError) as error:
        print(f"設定錯誤：{error}")
        return 1
    try:
        set_window_status("正在連接 Discord")
        bot.run(token, log_handler=None)
    except discord.LoginFailure:
        print("Discord Bot Token 無效，請到 Developer Portal 檢查或重新產生。")
        return 1
    except discord.PrivilegedIntentsRequired:
        print("請到 Developer Portal > Bot 開啟 Message Content Intent，然後重新啟動。")
        return 1
    finally:
        set_window_status("已停止")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
