from app.db import fetch_all
from dotenv import load_dotenv
load_dotenv()
try:
    print(fetch_all("SELECT column_name FROM information_schema.columns WHERE table_name = 'translations'"))
except Exception as e:
    print(e)
