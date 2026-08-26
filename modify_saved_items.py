import re

file_path = "app/saved_items.py"
with open(file_path, "r", encoding="utf-8") as f:
    content = f.read()

# Replace list_saved_items to accept kind_group
old_func = """def list_saved_items(user_id: str) -> list[dict]:
    return fetch_all(
        "select id, kind, title, excerpt, created_at from saved_items "
        "where user_id = %s and kind in ('favorite_result', 'favorite_section') order by created_at desc",
        [user_id],
    )"""

new_func = """def list_saved_items(user_id: str, kind_group: str = 'history') -> list[dict]:
    if kind_group == 'favorites':
        kinds = ('favorite_result', 'favorite_section')
    else:
        kinds = ('result', 'section')
        
    return fetch_all(
        "select id, kind, title, excerpt, created_at from saved_items "
        "where user_id = %s and kind = ANY(%s) order by created_at desc",
        [user_id, list(kinds)],
    )"""

if old_func in content:
    content = content.replace(old_func, new_func)

with open(file_path, "w", encoding="utf-8") as f:
    f.write(content)
