"""Cloud mode is configured by Render environment variables."""
import os

def read_mode():
    return "all" if os.getenv("REPLY_MODE", "mention").strip().lower() == "all" else "mention"
