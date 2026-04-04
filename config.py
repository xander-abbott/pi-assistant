import os
from dotenv import load_dotenv

load_dotenv(dotenv_path=os.path.join(os.path.dirname(__file__), ".env"))

BOT_TOKEN = os.environ["BOT_TOKEN"]
CHAT_ID   = int(os.environ["CHAT_ID"])
DB_PATH   = os.environ.get("DB_PATH", "/home/pi/assistant/data.db")
LOG_LEVEL = os.environ.get("LOG_LEVEL", "INFO")
