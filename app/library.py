"""Thư viện tài liệu: cây thư mục do Admin tự dựng, chứa file PDF (Tam Tạng, Chú Giải,
Phụ Chú Giải, Ngoại điển, Từ điển Pali...).

Cây linh hoạt (bảng `library_nodes`, xem `db/migrations/008_document_library.sql`): một
node `folder` chứa thư mục con hoặc file ở bất kỳ độ sâu nào, không ép cứng số tầng.

File PDF nằm trên đĩa VPS tại LIBRARY_FILES_DIR, ngoài git (`*.pdf` đã bị .gitignore chặn
từ trước). DB chỉ lưu tên file đã sinh bằng uuid (`file_path`) - tên vật lý tách khỏi tên
hiển thị `name` admin nhập, tránh trùng tên và path traversal từ tên gốc người dùng tải lên.

Nhãn nút vào thư viện ở trang chủ là một dòng text admin sửa được, lưu app/data/
library_settings.json - giống notice.py, vì đây chỉ là một giá trị đơn, không đáng một
migration riêng và schema DB do dự án Next.js sở hữu (xem CLAUDE.md).
"""

import json
import uuid
from pathlib import Path
from threading import Lock

from .config import PYTHON_DIR
from .db import execute, fetch_all, fetch_one

LIBRARY_FILES_DIR = PYTHON_DIR / "app" / "data" / "library_files"
SETTINGS_FILE = PYTHON_DIR / "app" / "data" / "library_settings.json"
_SETTINGS_LOCK = Lock()

DEFAULT_BUTTON_LABEL = "Tổng hợp các bản dịch tam tạng, chú giải, phụ chú giải"


def _row_to_node(row: dict) -> dict:
    return {
        "id": str(row["id"]),
        "parentId": str(row["parent_id"]) if row.get("parent_id") else None,
        "nodeType": row["node_type"],
        "name": row["name"],
        "fileSizeBytes": row.get("file_size_bytes"),
        "createdAt": row.get("created_at"),
    }


def list_children(parent_id: str | None) -> list[dict]:
    """Sắp theo `sort_order` - admin tự kéo-thả để đổi vị trí (xem `reorder_children`),
    mặc định là thứ tự TẢI LÊN (cũ trước, mới sau) cho tới khi admin sắp lại tay. Không
    còn ép "thư mục luôn trước file": kéo-thả cho phép xen kẽ tự do nếu admin muốn."""
    rows = fetch_all(
        """
        select id, parent_id, node_type, name, file_size_bytes, created_at
        from library_nodes
        where parent_id is not distinct from %s
        order by sort_order asc, created_at asc
        """,
        [parent_id],
    )
    return [_row_to_node(row) for row in rows]


def _has_subfolder(node_id: str) -> bool:
    row = fetch_one(
        "select 1 from library_nodes where parent_id = %s and node_type = 'folder' limit 1",
        [node_id],
    )
    return row is not None


def list_children_for_browse(parent_id: str | None) -> list[dict]:
    """Như `list_children`, nhưng đánh dấu thêm `isLeafFolder` cho mỗi thư mục con và
    nạp sẵn `leafFiles` nếu đúng là thư mục áp chót (bên trong toàn file, không còn thư
    mục con nào nữa).

    Trang `/library` dùng cờ này để quyết định thư mục nào xổ danh sách PDF ngay tại
    chỗ (`<details>`, không tải lại trang) và thư mục nào vẫn phải chuyển trang - một
    thư mục còn chứa thư mục con thì chưa "chạm đáy" nên vẫn cần đi tiếp.
    """
    children = list_children(parent_id)
    for item in children:
        if item["nodeType"] != "folder":
            continue
        item["isLeafFolder"] = not _has_subfolder(item["id"])
        if item["isLeafFolder"]:
            item["leafFiles"] = list_children(item["id"])
    return children


def get_node(node_id: str) -> dict | None:
    row = fetch_one(
        "select id, parent_id, node_type, name, file_size_bytes, created_at from library_nodes where id = %s",
        [node_id],
    )
    return _row_to_node(row) if row else None


def get_folder_tree() -> list[dict]:
    """Toàn bộ cây thư mục (không gồm file) để vẽ sidebar/dropdown chọn đích - admin
    thấy hết mọi tầng cùng lúc thay vì phải bấm xuyên từng cấp mới biết cây đang có gì."""
    rows = fetch_all(
        "select id, parent_id, name from library_nodes where node_type = 'folder' order by sort_order asc, created_at asc"
    )
    by_parent: dict[str | None, list[dict]] = {}
    for row in rows:
        parent_key = str(row["parent_id"]) if row["parent_id"] else None
        by_parent.setdefault(parent_key, []).append({"id": str(row["id"]), "name": row["name"], "children": []})

    def attach(node: dict) -> dict:
        node["children"] = by_parent.get(node["id"], [])
        for child in node["children"]:
            attach(child)
        return node

    return [attach(node) for node in by_parent.get(None, [])]


def get_breadcrumb(node_id: str | None) -> list[dict]:
    """Chuỗi thư mục từ gốc tới node hiện tại (không gồm chính node nếu đó là file)."""
    chain: list[dict] = []
    current_id = node_id
    seen: set[str] = set()
    while current_id and current_id not in seen:
        seen.add(current_id)
        node = get_node(current_id)
        if not node:
            break
        chain.append(node)
        current_id = node["parentId"]
    chain.reverse()
    return chain


def _name_taken(parent_id: str | None, name: str, exclude_id: str | None = None) -> bool:
    row = fetch_one(
        """
        select 1 from library_nodes
        where parent_id is not distinct from %s and name = %s and id is distinct from %s
        """,
        [parent_id, name, exclude_id],
    )
    return row is not None


def _next_sort_order(parent_id: str | None) -> int:
    row = fetch_one(
        "select coalesce(max(sort_order), -1) + 1 as next_order from library_nodes "
        "where parent_id is not distinct from %s",
        [parent_id],
    )
    return int(row["next_order"]) if row else 0


def create_folder(parent_id: str | None, name: str) -> dict:
    name = name.strip()
    if not name:
        raise ValueError("Tên thư mục không được để trống.")
    if _name_taken(parent_id, name):
        raise ValueError("Đã có thư mục hoặc file cùng tên trong mục này.")
    row = fetch_one(
        """
        insert into library_nodes (parent_id, node_type, name, sort_order)
        values (%s, 'folder', %s, %s)
        returning id, parent_id, node_type, name, file_size_bytes, created_at
        """,
        [parent_id, name, _next_sort_order(parent_id)],
    )
    return _row_to_node(row)


def create_file_node(parent_id: str | None, name: str, stored_filename: str, size_bytes: int) -> dict:
    name = name.strip()
    if not name:
        raise ValueError("Tên file không được để trống.")
    if _name_taken(parent_id, name):
        raise ValueError("Đã có thư mục hoặc file cùng tên trong mục này.")
    row = fetch_one(
        """
        insert into library_nodes (parent_id, node_type, name, file_path, file_size_bytes, sort_order)
        values (%s, 'file', %s, %s, %s, %s)
        returning id, parent_id, node_type, name, file_size_bytes, created_at
        """,
        [parent_id, name, stored_filename, size_bytes, _next_sort_order(parent_id)],
    )
    return _row_to_node(row)


def reorder_children(parent_id: str | None, ordered_ids: list[str]) -> None:
    """Ghi lại thứ tự mới sau khi admin kéo-thả trong `/admin/library`.

    Chỉ những id thực sự là con của `parent_id` mới được cập nhật - JOIN không khớp
    được hàng nào với id lạ (gửi sai/giả mạo từ ngoài), nên tự động bỏ qua thay vì cần
    kiểm tra riêng.
    """
    if not ordered_ids:
        return
    execute(
        """
        update library_nodes as n
        set sort_order = v.pos - 1
        from unnest(%s::uuid[]) with ordinality as v(id, pos)
        where n.id = v.id and n.parent_id is not distinct from %s
        """,
        [ordered_ids, parent_id],
    )


def rename_node(node_id: str, name: str) -> dict:
    name = name.strip()
    if not name:
        raise ValueError("Tên không được để trống.")
    node = get_node(node_id)
    if not node:
        raise ValueError("Không tìm thấy mục này.")
    if _name_taken(node["parentId"], name, exclude_id=node_id):
        raise ValueError("Đã có thư mục hoặc file cùng tên trong mục này.")
    row = fetch_one(
        """
        update library_nodes set name = %s, updated_at = now()
        where id = %s
        returning id, parent_id, node_type, name, file_size_bytes, created_at
        """,
        [name, node_id],
    )
    return _row_to_node(row)


def _descendant_file_paths(node_id: str) -> list[str]:
    rows = fetch_all(
        """
        with recursive descendants as (
            select id, node_type, file_path from library_nodes where id = %s
            union all
            select n.id, n.node_type, n.file_path
            from library_nodes n
            join descendants d on n.parent_id = d.id
        )
        select file_path from descendants where node_type = 'file' and file_path is not null
        """,
        [node_id],
    )
    return [row["file_path"] for row in rows]


def delete_node(node_id: str) -> None:
    """Xoá node (và toàn bộ cây con nếu là thư mục). File vật lý bị xoá khỏi đĩa trước,
    vì `on delete cascade` chỉ dọn hàng trong DB chứ không đụng tới đĩa."""
    file_paths = _descendant_file_paths(node_id)
    execute("delete from library_nodes where id = %s", [node_id])
    for stored_filename in file_paths:
        path = LIBRARY_FILES_DIR / stored_filename
        try:
            path.unlink(missing_ok=True)
        except OSError:
            pass


def resolve_file_path(node: dict) -> Path | None:
    if node["nodeType"] != "file":
        return None
    row = fetch_one("select file_path from library_nodes where id = %s", [node["id"]])
    if not row or not row.get("file_path"):
        return None
    return LIBRARY_FILES_DIR / row["file_path"]


def format_size(num_bytes: int | None) -> str:
    if not num_bytes:
        return ""
    size = float(num_bytes)
    for unit in ("B", "KB", "MB", "GB"):
        if size < 1024:
            return f"{size:.0f} {unit}" if unit == "B" else f"{size:.1f} {unit}"
        size /= 1024
    return f"{size:.1f} GB"


def new_stored_filename() -> str:
    LIBRARY_FILES_DIR.mkdir(parents=True, exist_ok=True)
    return f"{uuid.uuid4().hex}.pdf"


def get_button_label() -> str:
    if not SETTINGS_FILE.exists():
        return DEFAULT_BUTTON_LABEL
    try:
        data = json.loads(SETTINGS_FILE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return DEFAULT_BUTTON_LABEL
    label = str((data or {}).get("buttonLabel") or "").strip()
    return label or DEFAULT_BUTTON_LABEL


def save_button_label(label: str) -> str:
    label = str(label or "").strip() or DEFAULT_BUTTON_LABEL
    with _SETTINGS_LOCK:
        SETTINGS_FILE.parent.mkdir(parents=True, exist_ok=True)
        SETTINGS_FILE.write_text(json.dumps({"buttonLabel": label}, ensure_ascii=False, indent=2), encoding="utf-8")
    return label
