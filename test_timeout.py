import time
from google import genai
import os
from dotenv import load_dotenv

load_dotenv()
api_key = os.getenv("GEMINI_API_KEY")

client = genai.Client(api_key=api_key, http_options={"timeout": 60.0})

start = time.time()
try:
    response = client.models.generate_content(
        model="gemini-2.5-flash",
        contents="hi",
    )
    print("Success")
except Exception as e:
    print(f"Failed in {time.time() - start} seconds: {e}")
