import os
from dotenv import load_dotenv

load_dotenv(dotenv_path=os.path.join(os.path.dirname(__file__), ".env"))

BOT_TOKEN = os.environ["BOT_TOKEN"]
DB_PATH   = os.environ.get("DB_PATH", "/home/xanderabbott/assistant/data/data.db")
LOG_LEVEL = os.environ.get("LOG_LEVEL", "INFO")
