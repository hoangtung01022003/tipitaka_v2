from app.main import app
from fastapi.testclient import TestClient

client = TestClient(app)
response = client.post("/search-page", data={"q": "dūtakamma", "corpus_type": "all", "pitaka_type": "all", "lang": "vi"})
print(response.status_code)
if response.status_code == 500:
    print(response.text)
