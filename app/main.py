from pathlib import Path

from fastapi import FastAPI, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from .db import fetch_all, fetch_one
from .search_engine import search_passages
from .translator import translate_passage


APP_DIR = Path(__file__).resolve().parent

app = FastAPI(title="Tipitaka Python Search")
app.mount("/static", StaticFiles(directory=APP_DIR / "static"), name="static")
templates = Jinja2Templates(directory=APP_DIR / "templates")


CORPUS_OPTIONS = [
    {"value": "mul", "label": "Tạng kinh", "description": "Tipitaka Mula, gồm Tam tạng gốc"},
    {"value": "att", "label": "Chú giải", "description": "Atthakatha"},
    {"value": "tik", "label": "Phụ chú giải", "description": "Tika"},
    {"value": "nrf", "label": "Ngoại điển", "description": "Anna"},
]
PITAKA_OPTIONS = [
    {"value": "vinaya", "label": "Tạng Luật", "description": "Vinayapitaka"},
    {"value": "sutta", "label": "Tạng Kinh", "description": "Suttapitaka"},
    {"value": "abhidhamma", "label": "Tạng Vi Diệu Pháp", "description": "Abhidhammapitaka"},
]


@app.get("/", response_class=HTMLResponse)
def index(request: Request):
    return templates.TemplateResponse(
        "index.html",
        {
            "request": request,
            "corpus_options": CORPUS_OPTIONS,
            "pitaka_options": PITAKA_OPTIONS,
            "default_query": "Tìm cho tôi bài kinh nói về quả phước của sự bố thí",
        },
    )


@app.post("/search")
def search_api(payload: dict):
    query = str(payload.get("query", "")).strip()
    filters = payload.get("filters") or {}
    corpus_types = filters.get("corpusType") or ["mul"]
    pitaka_type = filters.get("pitakaType")
    page = int(payload.get("page") or 1)
    page_size = min(20, int(payload.get("pageSize") or 5))
    if not query:
        raise HTTPException(status_code=400, detail="Missing query.")
    return search_passages(query, corpus_types, pitaka_type, page, page_size)


@app.post("/search-page", response_class=HTMLResponse)
def search_page(
    request: Request,
    query: str = Form(...),
    corpus_type: str = Form(...),
    pitaka_type: str | None = Form(None),
    page: int = Form(1),
):
    result = search_passages(query, [corpus_type], pitaka_type or None, page, 5)
    return templates.TemplateResponse(
        "results.html",
        {
            "request": request,
            "result": result,
            "query": query,
            "corpus_type": corpus_type,
            "pitaka_type": pitaka_type,
        },
    )


@app.get("/api/passages/{passage_id}")
def passage_api(passage_id: str):
    row = fetch_one(
        "select id, document_id, sort_order, paragraph_no, pali_text, hierarchy from passages where id = %s",
        [passage_id],
    )
    if not row:
        raise HTTPException(status_code=404, detail="Passage not found.")

    nearby = fetch_all(
        """
        select id, paragraph_no, pali_text
        from passages
        where document_id = %s and sort_order between %s and %s and id <> %s
        order by sort_order asc
        """,
        [row["document_id"], row["sort_order"] - 2, row["sort_order"] + 2, row["id"]],
    )
    translation = translate_passage(passage_id)
    source_path = row.get("hierarchy", {}).get("sourcePath")
    return {
        "id": str(row["id"]),
        "sourcePath": " -> ".join(source_path) if isinstance(source_path, list) else "",
        "paragraphNo": row["paragraph_no"],
        "paliText": row["pali_text"],
        "translation": translation,
        "nearbyPassages": nearby,
    }


@app.exception_handler(Exception)
def handle_exception(_request: Request, exc: Exception):
    status = exc.status_code if isinstance(exc, HTTPException) else 500
    detail = exc.detail if isinstance(exc, HTTPException) else str(exc)
    return JSONResponse({"error": detail}, status_code=status)
