# Discord DeepSeek Bot — Render 雲端版

獨立於本機版。一般 @ 聊天、圖片理解、DeepSeek 主動搜尋保留；`/生成` 兩個選項都只回覆「現在為雲端板無法使用該功能」，不呼叫本機 agy 或 Google API。

## Render

用 Blueprint 匯入本儲存庫的 render.yaml。方案為免費 Web Service，使用 `pip install -r requirements.txt` 建置、`python -u cloud_main.py` 啟動。環境變數必須填 DISCORD_BOT_TOKEN 與 DEEPSEEK_API_KEY，不可把金鑰上傳 GitHub。健康檢查為 /healthz，PORT 由 Render 注入。

免費服務閒置 15 分鐘會休眠，Discord Gateway 不保證維持免費 Web Service 活躍，因此不保證 24 小時在線。若要持續在線，需另選不休眠的付費服務。

REPLY_MODE=mention 為僅 @ 回覆，all 為全回。ALLOWED_CHANNEL_IDS 可限制頻道。重啟後短期上下文清空，無長期記憶。

同一 Discord Token 不應同時運行本機與雲端版，避免重複回覆；兩份程式也會同步同一組應用程式指令。正式切換時先停止本機 Bot，或為雲端版建立另一個 Discord Bot Token。
