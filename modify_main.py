import re

file_path = "app/main.py"
with open(file_path, "r", encoding="utf-8") as f:
    content = f.read()

favorites_endpoint = """
@app.get("/favorites", response_class=HTMLResponse)
def favorites_page(request: Request, lang: str | None = Query(None), user: dict = Depends(auth.require_user_page)):
    language = request_language(request, lang)
    items = saved_items.list_saved_items(str(user["id"]), kind_group="favorites")
    return templates.TemplateResponse(
        "saved.html",
        _template_context(request, language, user=user, items=items, is_favorites=True),
    )
"""

if "@app.get(\"/favorites\"" not in content:
    content = content.replace(
        '@app.get("/saved", response_class=HTMLResponse)\n',
        favorites_endpoint + '\n\n@app.get("/saved", response_class=HTMLResponse)\n'
    )

with open(file_path, "w", encoding="utf-8") as f:
    f.write(content)
