import asyncio
from app.main import _section_payload
from app.translator import summarize_section_text
import time

start = time.time()
section = _section_payload('bee3cd5a-a874-4737-b669-5d4e20fe4804')
res = summarize_section_text(section, 'vi')
print(f"Time: {time.time()-start}")
print("Result:", res)
