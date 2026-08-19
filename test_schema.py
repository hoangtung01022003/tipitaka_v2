from google import genai
import os
from dotenv import load_dotenv

load_dotenv()
api_key = os.getenv("GEMINI_API_KEY")

client = genai.Client(api_key=api_key, http_options={"timeout": 5.0})

from pydantic import BaseModel

class SummaryPoint(BaseModel):
    summary_text: str
    passage_ids: list[str]

class SectionSummary(BaseModel):
    points: list[SummaryPoint]

try:
    response = client.models.generate_content(
        model="gemini-2.5-flash",
        contents="Hello, summarize this text: The quick brown fox jumps over the lazy dog.",
        config=genai.types.GenerateContentConfig(
            response_mime_type="application/json",
            response_schema=SectionSummary,
            temperature=0.2,
        ),
    )
    print("Success:", response.text)
except Exception as e:
    import traceback
    traceback.print_exc()
