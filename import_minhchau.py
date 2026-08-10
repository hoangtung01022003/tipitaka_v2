"""Nạp bản dịch tiếng Việt của Hòa Thượng Thích Minh Châu.

NGUỒN: kho `sc-data` của SuttaCentral (`html_text/vi/pli/sutta/...`), KHÔNG dùng file PDF.
Riêng ba bộ Tiểu Bộ (iti, ud, snp) lấy từ budsas.org, xem `budsas.py` - `html_text/vi`
của SuttaCentral chỉ có Trường / Trung / Tương Ưng / Tăng Chi cộng đúng 3 bài Kinh Tập,
không có một dòng nào của Phật Thuyết Như Vậy và Phật Tự Thuyết.

Vì sao không dùng PDF khách gửi:
- PDF Trung Bộ có ~6% số từ bị vỡ chữ do lỗi dàn trang ("t ự tri", "gi ải thoát"),
  còn bản trên SuttaCentral thì sạch hoàn toàn.
- PDF Tăng Chi và Tương Ưng KHÔNG in tên Pali của bài kinh, nên không có gì để neo
  vào kinh gốc; bản trên SuttaCentral thì đặt tên file thẳng theo mã bài kinh.

CÁCH GHÉP - khác với bản Sujato ở một điểm quan trọng
-----------------------------------------------------
Bản Sujato dùng chung mã segment với Pali nên ghép được tới TỪNG CÂU.
Bản Minh Châu thì không: Ngài chia đoạn theo cách riêng, không trùng cách chia của
bản gốc CST. Cố ghép từng đoạn sẽ lệch (đã đo trên bản Tịnh Sự: lệch 19 đoạn).

Nên ở đây chỉ ghép tới CẤP BÀI KINH, đúng như khách đề xuất: đoạn trích ngắn thì để
AI dịch, còn bản dịch của các Ngài chỉ hiện khi người đọc mở toàn bộ bài kinh.
Toàn văn bài kinh được gắn vào đoạn ĐẦU TIÊN của bài, và giao diện đọc nó ở cấp mục.

Cấu trúc neo (`build_targets`) và lưới an toàn so tên bài kinh dùng lại nguyên của
`import_sujato.py` - đó là phần đã chặn được 220 bài lệch số thứ tự.

Chạy:
    python import_minhchau.py dn mn sn an
    python import_minhchau.py mn --limit 10 --dry-run --verbose
"""

import argparse
import collections
import html as html_lib
import json
import re
import sys
import urllib.error
import urllib.request
from pathlib import Path

# line_buffering: khong co no thi Python dem stdout khi chay nen/ghi ra file,
# nhin vao thay rong va tuong la treo.
sys.stdout.reconfigure(encoding="utf-8", line_buffering=True)

import budsas
from app.db import execute, fetch_all
from import_sujato import NIKAYA_CONFIG, bilara_header_titles, build_targets, fetch_json, titles_agree

SC_RAW = "https://raw.githubusercontent.com/suttacentral/sc-data/master/html_text/vi/pli/sutta"
SC_TREE = "https://api.github.com/repos/suttacentral/sc-data/git/trees/master?recursive=1"
BILARA_RAW = "https://raw.githubusercontent.com/suttacentral/bilara-data/published"
SOURCE_ID = "minh_chau"
LANGUAGE = "vi"

# Ba bộ SuttaCentral không có tiếng Việt, lấy từ budsas.org.
BUDSAS_NIKAYAS = {"iti", "ud", "snp"}

# Chỗ budsas gộp bài, khai báo rõ để lưới đếm bên dưới không báo động nhầm:
# hai bài kệ kết của phẩm Pārāyana (Pārāyanatthutigāthā, Pārāyanānugītigāthā) được
# budsas in chung dưới một mục "Kết luận", nên phẩm 5 của Kinh Tập thiếu đúng 1 bài.
BUDSAS_SHORTFALL = {"snp5": 1}

_TREE: dict[str, str] = {}


def sc_tree() -> dict[str, str]:
    """uid -> đường dẫn file tiếng Việt. Lấy nguyên cây thư mục về, khỏi đoán đường dẫn."""
    if _TREE:
        return _TREE
    data = fetch_json(SC_TREE)
    if not data:
        raise RuntimeError("Không tải được cây thư mục sc-data.")
    prefix = "html_text/vi/pli/sutta/"
    for item in data.get("tree", []):
        path = str(item.get("path") or "")
        if path.startswith(prefix) and path.endswith(".html"):
            _TREE[path.rsplit("/", 1)[-1][:-5]] = path[len(prefix) :]
    return _TREE


def fetch_html(url: str) -> str | None:
    try:
        with urllib.request.urlopen(url, timeout=60) as response:
            return response.read().decode("utf-8")
    except urllib.error.HTTPError:
        return None
    except Exception:  # noqa: BLE001
        return None


# Ban tren SuttaCentral chen moc "SC 1", "PTS 1.2", "TTC 3"... de doi chieu an ban in.
# Doc lien mach thi chung la rac, nen bo di.
# Khong dat \b o cuoi: moc thuong dinh lien chu ke tiep ("SC 1Mot thoi"), co \b thi truot.
_EDITION_MARK = re.compile(r"\b(?:SC|PTS|TTC|MS|BJT|VRI)\s*\d+(?:[.\-]\d+)*")
_TAG = re.compile(r"<[^>]+>")


def html_to_text(raw: str) -> str:
    """Bóc HTML thành văn xuôi, giữ ranh giới đoạn."""
    text = re.sub(r"(?is)<(script|style).*?</\1>", " ", raw)
    text = re.sub(r"(?i)</p\s*>|<br\s*/?>", "\n", text)
    text = _TAG.sub("", text)
    text = html_lib.unescape(text)
    text = _EDITION_MARK.sub(" ", text)
    lines = [re.sub(r"[ \t]+", " ", line).strip() for line in text.split("\n")]
    return "\n".join(line for line in lines if line)


def _group_key(uid: str) -> str:
    """'snp5.12' -> 'snp5', 'iti97' -> 'iti'. Khoá để đếm số bài mỗi phẩm."""
    return uid.rsplit(".", 1)[0] if "." in uid else re.sub(r"\d+$", "", uid)


def budsas_by_uid(nikaya: str, targets: list[dict]) -> dict[str, str] | None:
    """Bản budsas gán vào uid theo THỨ TỰ, sau khi đối chiếu số bài từng phẩm.

    budsas không ghi mã bài kinh, chỉ đánh số La Mã (mà lại in sai vài chỗ), nên
    chỗ duy nhất neo được là thứ tự đọc. Thứ tự chỉ đúng khi mỗi phẩm hai bên có
    cùng số bài - lệch một bài là mọi bài sau nó ghép sai mà không có triệu chứng
    gì, đúng kiểu lỗi mà lưới so tên bài của `import_sujato` sinh ra để chặn. Nên
    lệch là bỏ cả bộ, chứ không ghép phần khớp được.
    """
    grouped = bool(NIKAYA_CONFIG[nikaya].get("group_suffix"))
    texts: dict[str, str] = {}
    for sutta in budsas.fetch_texts(nikaya, grouped=grouped):
        uid = f"{nikaya}{sutta['group']}.{sutta['index']}" if grouped else f"{nikaya}{sutta['index']}"
        texts[uid] = sutta["text"]

    db_shape = collections.Counter(_group_key(target["uid"]) for target in targets)
    src_shape = collections.Counter(_group_key(uid) for uid in texts)
    gaps = [
        f"{key}: DB {count} bài / budsas {src_shape.get(key, 0)} bài"
        for key, count in sorted(db_shape.items())
        if src_shape.get(key, 0) != count - BUDSAS_SHORTFALL.get(key, 0)
    ]
    if gaps:
        print("  !! lệch số bài giữa budsas và DB, bỏ qua bộ này để tránh ghép sai")
        for gap in gaps:
            print(f"      {gap}")
        return None
    return texts


def main() -> None:
    parser = argparse.ArgumentParser(description="Nạp bản dịch tiếng Việt của HT. Thích Minh Châu.")
    parser.add_argument("nikayas", nargs="*", help=f"Bộ kinh: {', '.join(sorted(NIKAYA_CONFIG))}")
    parser.add_argument("--all", action="store_true")
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args()

    nikayas = sorted(NIKAYA_CONFIG) if args.all else args.nikayas
    if not nikayas:
        parser.error("cần chỉ định bộ kinh, hoặc dùng --all")

    if not args.dry_run:
        migration = Path(__file__).resolve().parents[1] / "db" / "migrations" / "002_human_translations.sql"
        execute(migration.read_text(encoding="utf-8"))
        print(f"đã áp dụng {migration.name}\n")

    # Ba bộ budsas không cần cây thư mục này, chỉ gọi tới khi thật sự có bộ dùng nó.
    if any(nikaya not in BUDSAS_NIKAYAS for nikaya in nikayas):
        print(f"SuttaCentral có {len(sc_tree())} bài kinh tiếng Việt\n")

    grand = {"suttas": 0, "written": 0, "missing": 0, "mismatch": 0, "chars": 0}

    for nikaya in nikayas:
        targets = build_targets(nikaya)
        config = NIKAYA_CONFIG[nikaya]
        expect = config.get("expect")
        print(f"=== {nikaya.upper()}: DB có {len(targets)} bài kinh" + (f" (kinh điển: {expect})" if expect else "") + " ===")
        if expect and len(targets) != expect:
            print("  !! lệch số bài kinh, bỏ qua bộ này để tránh ghép sai\n")
            continue

        # Lấy trọn bộ TRƯỚC khi cắt theo --limit: lưới đếm số bài mỗi phẩm chỉ có
        # nghĩa khi so trên đủ danh sách.
        budsas_texts = None
        if nikaya in BUDSAS_NIKAYAS:
            budsas_texts = budsas_by_uid(nikaya, targets)
            if budsas_texts is None:
                print()
                continue
            print(f"  nguồn: budsas.org · {len(budsas_texts)} bài")

        if args.limit:
            targets = targets[: args.limit]

        stats = {"suttas": 0, "written": 0, "missing": [], "mismatch": [], "chars": 0}

        for target in targets:
            uid = target["uid"]
            text = budsas_texts.get(uid, "") if budsas_texts is not None else ""
            if len(text) < 80:
                # Bài nào budsas không in lại (Sela và Vāseṭṭha chỉ ghi "xem Trung Bộ")
                # thì thử nốt SuttaCentral - `kn/snp/chau` cũng là bản Minh Châu.
                rel = sc_tree().get(uid)
                raw = fetch_html(f"{SC_RAW}/{rel}") if rel else None
                text = html_to_text(raw) if raw else text
            if len(text) < 80:
                stats["missing"].append(uid)
                continue

            # Luoi an toan: doi chieu ten bai kinh Pali (lay tu bilara) voi tieu de trong DB.
            root = fetch_json(f"{BILARA_RAW}/root/pli/ms/{_bilara_stem(uid)}_root-pli-ms.json") if _bilara_stem(uid) else None
            if root:
                headers = bilara_header_titles(root, uid)
                if headers and not titles_agree(headers, target["db_title"]):
                    stats["mismatch"].append(f"{uid} ({' / '.join(headers[1:3])} != {target['db_title']})")
                    continue

            # Gan toan van vao doan DAU TIEN cua bai kinh: ban dich khong chia doan
            # giong ban goc nen khong the rai deu tung doan ma khong sai.
            anchor = fetch_all(
                """
                select id from passages
                where document_id = %s and sort_order between %s and %s
                order by sort_order limit 1
                """,
                [target["document_id"], target["start"], target["end"]],
            )
            if not anchor:
                stats["missing"].append(uid)
                continue

            stats["suttas"] += 1
            stats["chars"] += len(text)
            if not args.dry_run:
                # Ghi kem KHOANG sort_order cua ca bai kinh: ban dich la cua ca bai,
                # phai tra duoc tu bat ky doan nao trong bai chu khong chi doan dau.
                execute(
                    """
                    insert into human_translations
                      (passage_id, source, language, translated_text, source_ref, segment_ids,
                       document_id, start_sort_order, end_sort_order)
                    values (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                    on conflict (passage_id, source) do update
                      set translated_text = excluded.translated_text,
                          source_ref = excluded.source_ref,
                          document_id = excluded.document_id,
                          start_sort_order = excluded.start_sort_order,
                          end_sort_order = excluded.end_sort_order,
                          updated_at = now()
                    """,
                    [anchor[0]["id"], SOURCE_ID, LANGUAGE, text, uid, [],
                     target["document_id"], target["start"], target["end"]],
                )
                stats["written"] += 1

            if args.verbose:
                print(f"     {uid:<10} {target['db_title'][:34]:<36} {len(text):>7} ký tự")

        print(f"  {stats['suttas']} bài · {stats['chars']:,} ký tự · ghi {stats['written']} bản dịch")
        if stats["missing"]:
            source_label = "budsas.org lẫn SuttaCentral" if budsas_texts is not None else "SuttaCentral"
            print(f"  không có trên {source_label}: {len(stats['missing'])} bài")
        if stats["mismatch"]:
            print(f"  BỎ QUA vì lệch tên: {len(stats['mismatch'])} bài")
            for item in stats["mismatch"][:4]:
                print(f"      {item[:104]}")

        if not args.dry_run and stats["suttas"]:
            execute(
                """
                insert into human_translation_imports
                  (source, language, scope, segments_total, segments_matched, passages_written, notes)
                values (%s, %s, %s, %s, %s, %s, %s)
                """,
                [SOURCE_ID, LANGUAGE, nikaya, stats["suttas"], stats["suttas"], stats["written"],
                 f"thiếu {len(stats['missing'])}, lệch tên {len(stats['mismatch'])}"],
            )

        grand["suttas"] += stats["suttas"]
        grand["written"] += stats["written"]
        grand["missing"] += len(stats["missing"])
        grand["mismatch"] += len(stats["mismatch"])
        grand["chars"] += stats["chars"]
        print()

    print(f"TỔNG: {grand['suttas']} bài kinh · {grand['chars']:,} ký tự · ghi {grand['written']}"
          f" · thiếu {grand['missing']} · lệch tên {grand['mismatch']}")


def _bilara_stem(uid: str) -> str | None:
    from import_sujato import bilara_tree

    return bilara_tree().get(uid)


if __name__ == "__main__":
    main()
