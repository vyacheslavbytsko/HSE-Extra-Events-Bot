import os

from dotenv import load_dotenv

load_dotenv()

tg_token = os.getenv('TG_TOKEN')
gigachat_token = os.getenv("GIGACHAT_TOKEN")