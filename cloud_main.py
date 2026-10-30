"""Render entry point: Discord Gateway client plus a readiness endpoint."""
import asyncio
import logging
import os
import signal
from pathlib import Path
from aiohttp import web
from bot import ChatBot, bounded_int


async def run():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    token = os.getenv("DISCORD_BOT_TOKEN", "").strip()
    key = os.getenv("DEEPSEEK_API_KEY", "").strip()
    if not token or not key:
        raise RuntimeError("Render 必須設定 DISCORD_BOT_TOKEN 和 DEEPSEEK_API_KEY")
    allowed = {int(v.strip()) for v in os.getenv("ALLOWED_CHANNEL_IDS", "").split(",") if v.strip()}
    bot = ChatBot(key, os.getenv("DEEPSEEK_MODEL", "deepseek-flash"), allowed,
        bounded_int("CONTEXT_MESSAGES",20,1,50), bounded_int("COOLDOWN_SECONDS",5,1,300),
        bounded_int("MAX_REPLY_TOKENS",800,50,2000),
        Path(__file__).with_name("persona.txt").read_text(encoding="utf-8"))
    app = web.Application()
    async def health(request):
        return web.json_response({"status": "ready" if bot.is_ready() else "connecting"},
                                 status=200 if bot.is_ready() else 503)
    app.router.add_get("/healthz", health)
    app.router.add_get("/", health)
    runner = web.AppRunner(app)
    await runner.setup()
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, stop.set)
        except NotImplementedError:
            pass
    task = None
    stopper = None
    try:
        await web.TCPSite(runner, "0.0.0.0", int(os.getenv("PORT", "10000"))).start()
        task = asyncio.create_task(bot.start(token))
        stopper = asyncio.create_task(stop.wait())
        done, _ = await asyncio.wait({task, stopper}, return_when=asyncio.FIRST_COMPLETED)
        if task in done:
            await task
    finally:
        await bot.close()
        if task and not task.done():
            task.cancel()
        if stopper:
            stopper.cancel()
        await asyncio.gather(*(t for t in (task,stopper) if t), return_exceptions=True)
        await runner.cleanup()


if __name__ == "__main__":
    asyncio.run(run())
