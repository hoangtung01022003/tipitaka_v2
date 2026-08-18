import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from import_indacanda import (
    group_indented_matches,
    match_indented_pairs,
    mend_spacing,
    split_indented_paragraphs,
    split_verses,
)
from import_sujato import _clean, merged_segment_text
from indacanda_full_extract import (
    HeadingHit,
    Unit,
    clean_vietnamese_page,
    find_vietnamese_heading_offset,
    heading_stem,
)
from apply_indacanda_full_preview import load_verified_volume


class IndacandaPdfParsingTests(unittest.TestCase):
    def test_bare_page_number_is_not_treated_as_a_verse_number(self):
        self.assertEqual(split_verses("4\nEvaṃ me sutaṃ.\nYo hi koci āvuso."), {})

    def test_pts2_layout_parser_uses_indents_and_stops_before_footnotes(self):
        layout = (
            "                                      I. YUGANADDHAKATHĀ\n\n"
            "           Evaṃ me sutaṃ: Ekaṃ samayaṃ āyasmā Ānando Kosambiyaṃ\n"
            "viharati Ghositārāme. Tatra kho āyasmā Ānando etadavoca.\n"
            "           Yo hi koci āvuso bhikkhu vā bhikkhunī vā mama santike\n"
            "arahattapattaṃ byākaroti. Katamehi catūhi?\n\n"
            "1 arahantappattiṃ - Ani; arahattaṃ - Syā.\n"
            "                                                                                                                                 2\n"
        )

        paragraphs = split_indented_paragraphs(layout)

        self.assertEqual(len(paragraphs), 2)
        self.assertTrue(paragraphs[0].startswith("Evaṃ me sutaṃ"))
        self.assertTrue(paragraphs[1].startswith("Yo hi koci"))
        self.assertNotIn("arahantappattiṃ - Ani", " ".join(paragraphs))

    def test_pts2_matcher_repairs_split_glyphs_and_keeps_book_order(self):
        passages = [
            {
                "id": "p1",
                "sort_order": 1167,
                "normalized_pali": (
                    "evam me sutam ekam samayam ayasma anando kosambiyam "
                    "viharati ghositarame tatra kho etadavoca"
                ),
            },
            {
                "id": "p2",
                "sort_order": 1168,
                "normalized_pali": (
                    "yo hi koci avuso bhikkhu va bhikkhuni va mama santike "
                    "arahattapattam byakaroti katamehi catuhi"
                ),
            },
            {
                "id": "distractor",
                "sort_order": 1400,
                "normalized_pali": (
                    "sabbasankharesu aniccanupassi viharati dukkhanupassi "
                    "anattanupassi nibbidabahulo"
                ),
            },
        ]
        aligned = [
            (
                "Eva ṃ me suta ṃ: Eka ṃ samaya ṃ āyasmā Ānando Kosambiya ṃ "
                "viharati Ghosit ārāme. Tatra kho etadavoca.",
                "Tôi đã được nghe như vầy.",
                None,
            ),
            (
                "Yo hi koci ā vuso bhikkhu vā bhikkhunī vā mama santike "
                "arahattapatta ṃ by ākaroti. Katamehi catūhi?",
                "Này các đại đức, bất cứ vị tỳ khưu nào.",
                None,
            ),
        ]

        matches, quality_count = match_indented_pairs(aligned, passages)

        self.assertEqual(quality_count, 2)
        self.assertEqual([match["passage_id"] for match in matches], ["p1", "p2"])
        self.assertEqual([match["sort_order"] for match in matches], [1167, 1168])

    def test_pts2_grouping_does_not_drop_later_printed_parts_of_one_passage(self):
        aligned = [
            ("Pali one", "Phần m ột.", None),
            ("Pali two", "Phần hai.", None),
        ]
        matches = [
            {"pair_index": 0, "passage_id": "p1", "sort_order": 10, "score": 0.98},
            {"pair_index": 1, "passage_id": "p1", "sort_order": 10, "score": 0.91},
        ]

        grouped = group_indented_matches(matches, aligned)

        self.assertEqual(len(grouped), 1)
        self.assertEqual(grouped[0]["pair_indexes"], [0, 1])
        self.assertEqual(grouped[0]["text"], "Phần một.\n\nPhần hai.")
        self.assertEqual(grouped[0]["score"], 0.91)

    def test_duplicate_number_replaces_uppercase_heading_with_real_first_paragraph(self):
        text = """
1. MAHĀPADĀNASUTTAṂ
1. Evaṃ me sutaṃ—ekaṃ samayaṃ Bhagavā Sāvatthiyaṃ viharati.
2. Atha kho āyasmato Ānandassa rahogatassa paṭisallīnassa.
1. footnote one that must not replace the real paragraph
"""

        verses = split_verses(text)

        self.assertTrue(verses["1"].startswith("Evaṃ me sutaṃ"))
        self.assertNotIn("footnote", verses["1"])

    def test_mend_spacing_covers_vietnamese_and_pali_split_shapes(self):
        broken = "đấng C hánh Đẳng Giác Sikh ī, vị U ttara ở t hành p hố."

        fixed = mend_spacing(broken)

        self.assertEqual(fixed, "đấng Chánh Đẳng Giác Sikhī, vị Uttara ở thành phố.")

    def test_mend_spacing_keeps_legitimate_word_boundaries(self):
        original = (
            "Y đã nói do ý ấy, vị Ānanda hiểu điều ấy mà ra. "
            "Này āvuso và Từ āvuso được giữ; ba y vẫn là ba chiếc y. "
            "Mahāpajāpati Gotamī, Tissa Metteyya và goose bumps là hai từ."
        )

        self.assertEqual(mend_spacing(original), original)

    def test_mend_spacing_covers_second_audit_batch(self):
        broken = (
            "An āthapiṇḍika gặp Moggall āna ở Gijjhak ūṭa; "
            "K aṇṭaka và Aṇī kadatta nghe thu yết về pariv āsa. "
            "Nước lu ộc, người không b ộp chộp và tất cả c ảc nghề."
        )

        fixed = mend_spacing(broken)

        self.assertEqual(
            fixed,
            "Anāthapiṇḍika gặp Moggallāna ở Gijjhakūṭa; "
            "Kaṇṭaka và Aṇīkadatta nghe thuyết về parivāsa. "
            "Nước luộc, người không bộp chộp và tất cả các nghề.",
        )
        self.assertEqual(mend_spacing(fixed), fixed)

    def test_mend_spacing_covers_remaining_confirmed_pdf_splits(self):
        broken = (
            "Người mẹ mi ễn cưỡng mang th ai; chim d ẽ có cánh s ọc và bị tắt ngh ẽn. "
            "Vị ấy tháo g ở mối rối, tạo ngi ệp bằng hành động b ắng trí tuệ. "
            "Đức Phật là vị Ch ủa; người thiểu trí nghĩ r ẵng như vậy. "
            "Đức Thế Tôn, b ậcTự Chủ, trả lời câu hỏi cắt đứt s ựPhân Vân. "
            "Ñ āṇadhara giảng k athā, g āthā và p ārivāsa. "
            "Thế gi an ho an hỷ; chúng sanh qu an sát chim b ươm bướm. "
            "Tên Ro ma sa, Samu dda, Ramm a, Somadev a, Khul u và làN an di sen a. "
            "Mùi Ta nh Hôi, kẻ giàu sa ng, người lu ồn cúi và xúi qu ẩy. "
            "Y sa ṅghāṭi; di ṭṭhi; sa ṅkhepato; M akuṭabandhana."
        )

        fixed = mend_spacing(broken)

        self.assertEqual(
            fixed,
            "Người mẹ miễn cưỡng mang thai; chim dẽ có cánh sọc và bị tắt nghẽn. "
            "Vị ấy tháo gỡ mối rối, tạo nghiệp bằng hành động bằng trí tuệ. "
            "Đức Phật là vị Chúa; người thiểu trí nghĩ rằng như vậy. "
            "Đức Thế Tôn, bậc Tự Chủ, trả lời câu hỏi cắt đứt sự Phân Vân. "
            "Ñāṇadhara giảng kathā, gāthā và pārivāsa. "
            "Thế gian hoan hỷ; chúng sanh quan sát chim bươm bướm. "
            "Tên Romasa, Samudda, Ramma, Somadeva, Khulu và là Nandisena. "
            "Mùi Tanh Hôi, kẻ giàu sang, người luồn cúi và xúi quẩy. "
            "Y saṅghāṭi; diṭṭhi; saṅkhepato; Makuṭabandhana.",
        )
        self.assertEqual(mend_spacing(fixed), fixed)

    def test_mend_spacing_repairs_confirmed_private_font_glyph(self):
        self.assertEqual(mend_spacing("pappoṭakojam\uf01e"), "pappoṭakojaṃ")

    def test_mend_spacing_repairs_final_whole_preview_audit(self):
        broken = (
            "ở kho ảng giữa, vượt chư ớng ngại trong trư ờng hợp ấy; "
            "Ngã ( đoạn kiến), điều ấy sẽ ) không xảy ra."
        )

        self.assertEqual(
            mend_spacing(broken),
            "ở khoảng giữa, vượt chướng ngại trong trường hợp ấy; "
            "Ngã (đoạn kiến), điều ấy sẽ) không xảy ra.",
        )
        self.assertEqual(mend_spacing("Tissa Metteyya"), "Tissa Metteyya")

    def test_mend_spacing_repairs_third_audit_batch(self):
        """Ba ca `audit_indacanda_spacing.py` chặn lại sau đợt vá 2.748 dòng.

        Cả ba đều KHÔNG sửa được bằng từ điển, mỗi ca một lý do khác nhau - xem chú
        thích tại `_KNOWN_PDF_REPLACEMENTS`.
        """
        # Chỗ cắt sai vị trí: nối thẳng ra "nghĩahội", không phải từ nào cả.
        self.assertEqual(
            mend_spacing("Thọ, theo ý ngh ĩahội tụ, là ly tham ái."),
            "Thọ, theo ý nghĩa hội tụ, là ly tham ái.",
        )
        # Tách chữ + PDF đọc sai dấu.
        self.assertEqual(
            mend_spacing("Dân chúng nói r ẳng: ‘Có các vị ẩn sĩ"),
            "Dân chúng nói rằng: ‘Có các vị ẩn sĩ",
        )
        # Vết tách thuần tuý mà từ điển bó tay vì "tu" và "ốt" đều là từ hợp lệ.
        self.assertEqual(
            mend_spacing("người đàn ông có thể tu ốt ra con rắn từ lớp da."),
            "người đàn ông có thể tuốt ra con rắn từ lớp da.",
        )

        # Và những cụm KHÔNG được đụng tới: đây là các chuỗi ký tự gần giống ba ca trên
        # nhưng hoàn toàn hợp lệ. Luật `(?<!\w)…(?!\w)` phải chặn chúng lại.
        for intact in (
            # Đây là lý do mục "tu ốt ra" phải lấy cả chữ "ra": "tu" là từ hợp lệ nên
            # một mục "tu ốt" trần sẽ nuốt luôn câu này.
            "vị ấy tu ốt đời trong rừng",
            "nghĩa hội tụ",         # đã đúng sẵn, không được sửa thêm
            "Dân chúng nói rằng:",  # đã đúng sẵn
            "một tháng tu ở đó",    # "tu" đứng riêng
        ):
            self.assertEqual(mend_spacing(intact), intact, intact)


class IndacandaWholePreviewTests(unittest.TestCase):
    def test_volume_specific_heading_aliases_do_not_loosen_global_matching(self):
        self.assertEqual(heading_stem("6. Dhammacariyasuttaṃ", "sn"), "kapilasuttam")
        self.assertEqual(heading_stem("X. Suññakathā", "pts2"), "sunnatakatha")
        self.assertEqual(heading_stem("I. Mahāpaññākathā", "pts2"), "pannakatha")
        self.assertEqual(heading_stem("1. Ajitamāṇavapucchā", "sn"), "ajitasuttam")

    def test_vietnamese_heading_offset_can_split_two_units_on_one_page(self):
        unit = Unit("s2", "doc", "2. Tissametteyyamāṇavapucchā", 5, 1, 2, [])
        page = (
            "2. KINH TISSAMETTEYYA\n"
            "1044. Nội dung bài thứ hai.\n"
            "3. KINH PUṆṆAKA\n"
            "1047. Nội dung bài thứ ba.\n"
        )

        cut = find_vietnamese_heading_offset(
            "sn", unit, HeadingHit(358, "2. TISSAMETTEYYASUTTAṂ1", 1.0), page
        )

        self.assertIsNotNone(cut)
        self.assertEqual(cut[0], 0)
        self.assertEqual(cut[1], "2. KINH TISSAMETTEYYA")

    def test_cleaner_drops_running_head_but_keeps_small_text(self):
        raw = (
            "Trường Bộ II - Đại Phẩm\n"
            "120. Nội dung chính.\n\n"
            "1 Chú thích chữ nhỏ thuộc bản gốc.\n"
        )

        cleaned = clean_vietnamese_page(raw, "dn2")

        self.assertNotIn("Trường Bộ II", cleaned)
        self.assertIn("120. Nội dung chính.", cleaned)
        self.assertIn("Chú thích chữ nhỏ", cleaned)

    def test_mend_spacing_repairs_confirmed_pts2_glyph_and_name_splits(self):
        broken = (
            "Bất cứ tưỏ ng nào đều không có cốt lỏ i. "
            "Đại đức Sārī - putta, Sañjī va và Khāṇ ukoṇḍañña."
        )

        self.assertEqual(
            mend_spacing(broken),
            "Bất cứ tưởng nào đều không có cốt lõi. "
            "Đại đức Sārī-putta, Sañjīva và Khāṇukoṇḍañña.",
        )


class IndacandaWholeApplyTests(unittest.TestCase):
    def test_verified_manifest_accepts_matching_hashes_and_rejects_tampered_txt(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            volume_dir = root / "dn2"
            volume_dir.mkdir()
            pdf_path = root / "11_D_02.pdf"
            pdf_path.write_bytes(b"test-pdf")
            text = "Bản dịch tiếng Việt đã được kiểm tra."
            text_path = volume_dir / "001_test.txt"
            text_path.write_text(text + "\n", encoding="utf-8")
            manifest = {
                "schema_version": 1,
                "mode": "preview_only_no_database_writes",
                "volume": "dn2",
                "source_pdf": str(pdf_path),
                "source_pdf_sha256": hashlib.sha256(b"test-pdf").hexdigest(),
                "total_units": 1,
                "pass_units": 1,
                "review_units": 0,
                "review_text_pages": 0,
                "items": [
                    {
                        "status": "PASS",
                        "title": "Test",
                        "section_id": "00000000-0000-0000-0000-000000000001",
                        "document_id": "00000000-0000-0000-0000-000000000002",
                        "start_sort_order": 1,
                        "end_sort_order": 2,
                        "vietnamese_pages": [2],
                        "text_characters": len(text),
                        "text_sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(),
                        "output_file": text_path.name,
                        "title_match_score": 1.0,
                    }
                ],
            }
            (volume_dir / "manifest.json").write_text(
                json.dumps(manifest, ensure_ascii=False), encoding="utf-8"
            )

            items, reviews, manifest_hash = load_verified_volume("dn2", root)

            self.assertEqual(len(items), 1)
            self.assertEqual(reviews, 0)
            self.assertEqual(len(manifest_hash), 64)
            text_path.write_text("đã bị sửa\n", encoding="utf-8")
            with self.assertRaisesRegex(RuntimeError, "TXT đã đổi"):
                load_verified_volume("dn2", root)


class SujatoCommentTests(unittest.TestCase):
    def test_clean_removes_embedded_bom(self):
        self.assertEqual(_clean("the role of bṛhaspa\ufeffti"), "the role of bṛhaspati")

    def test_comment_is_inserted_inline_without_an_added_label(self):
        text = merged_segment_text(
            ["dn14:1.10.1"],
            {"dn14:1.10.1": "The translated sentence."},
            {"dn14:1.10.1": "<i>Small text</i> &amp; detail."},
        )

        self.assertEqual(text, "The translated sentence.\nSmall text & detail.")
        self.assertNotIn("note", text.lower())


class WholeSuttaReaderTests(unittest.TestCase):
    def test_reader_unit_recognizes_sutta_and_vinaya_khandhaka(self):
        from app.main import _is_reader_unit_title

        self.assertTrue(_is_reader_unit_title("1. Mahāpadānasuttaṃ"))
        self.assertTrue(_is_reader_unit_title("2. Uposathakkhandhako"))
        self.assertTrue(_is_reader_unit_title("10. Nandasikkhāpadaṃ"))
        self.assertFalse(_is_reader_unit_title("3. Tikanipāta"))

    def test_unnumbered_katha_is_a_unit_but_book_and_preface_names_are_not(self):
        """Kathāvatthu và các bộ chú giải đặt tên mục con KHÔNG SỐ.

        Ca khách báo: `Atītacakkhurūpādikathā` (6 đoạn) nằm trong `5. Sabbamatthītikathā`
        (101 đoạn); mục con không số nên không được nhận và trang đọc mở ra cả 101 đoạn.

        Chỉ nới cho lớp NGỮ CẢNH nên nó không thể thắng một bài kinh - kiểm luôn chiều đó.
        """
        from app.main import READER_FALLBACK_MAX_PASSAGES, _is_reader_unit_row

        def row(title, span):
            return {"title": title, "start_sort_order": 0, "end_sort_order": span - 1}

        self.assertTrue(_is_reader_unit_row(row("Atītacakkhurūpādikathā", 6)))
        self.assertTrue(_is_reader_unit_row(row("Dhammuddesavārakathā", 20)))

        # Tên bộ chú giải và lời tựa cũng kết thúc bằng `kathā` nhưng KHÔNG phải mục để đọc.
        self.assertFalse(_is_reader_unit_row(row("Dasakanipāta-aṭṭhakathā", 167)))
        self.assertFalse(_is_reader_unit_row(row("Ganthārambhakathā", 3815)))
        # Quá lớn thì không phải đơn vị đọc, dù tên có đuôi hợp lệ.
        self.assertFalse(
            _is_reader_unit_row(row("Nidānakathā", READER_FALLBACK_MAX_PASSAGES + 1))
        )
        # Không mang hậu tố nào thì vẫn không phải đơn vị.
        self.assertFalse(_is_reader_unit_row(row("Naārammaṇapaccayo", 5)))

        # Bài kinh (lớp dứt khoát) phải thắng `kathā` không số dù nhỏ hơn nhiều.
        from app.main import _canonical_reader_section

        katha = {
            "id": "katha", "document_id": "doc", "title": "Pubbenivāsapaṭisaṃyuttakathā",
            "source_path": ["1. Mahāpadānasuttaṃ", "Pubbenivāsapaṭisaṃyuttakathā"],
            "start_sort_order": 4, "end_sort_order": 35,
        }
        sutta = {
            "id": "sutta", "document_id": "doc", "title": "1. Mahāpadānasuttaṃ",
            "source_path": ["1. Mahāpadānasuttaṃ"],
            "start_sort_order": 4, "end_sort_order": 207,
        }
        with patch("app.main.fetch_all", return_value=[katha, sutta]):
            self.assertEqual(_canonical_reader_section(dict(katha))["id"], "sutta")

    def test_overreaching_recorded_range_is_clipped_at_the_first_intruder(self):
        """Bản import XML ghi cho vài section phạm vi trọn cả tài liệu.

        Ca khách báo: `Ganthārambhakathā` trong `s0506a` ghi là 0-3814 (3.815 đoạn) nhưng
        lời tựa ấy chỉ có 29 đoạn - nội dung thật bắt đầu ở `1. Itthivimānaṃ` từ đoạn 29.
        Cắt tại đoạn mở đầu của section KHÔNG PHẢI con cháu đầu tiên; phạm vi sau khi cắt
        trùng khớp số đoạn thật ở cả ba ca đã đo (3.815->29, 3.892->90, 3.048->1).
        """
        from app.main import _clip_overreaching_range

        wrapper = {
            "id": "pref", "document_id": "doc", "title": "Ganthārambhakathā",
            "source_path": ["Vimānavatthu-Aṭṭhakathā", "Ganthārambhakathā"],
            "start_sort_order": 0, "end_sort_order": 3814,
        }
        # `1. Itthivimānaṃ` KHÔNG phải con cháu của lời tựa -> lời tựa không thể chứa nó.
        intruder = {
            "source_path": ["Vimānavatthu-Aṭṭhakathā", "1. Itthivimānaṃ"],
            "start_sort_order": 29,
        }
        with patch("app.main.fetch_all", return_value=[intruder]):
            clipped = _clip_overreaching_range(dict(wrapper))
        self.assertEqual(clipped["end_sort_order"], 28)
        self.assertTrue(clipped["_readerRangeClipped"])

        # Chiều ngược: cha HỢP LỆ không bị cắt, vì mục con của nó LÀ con cháu.
        parent = {
            "id": "unit", "document_id": "doc", "title": "1. Suttantabhājanīyaṃ",
            "source_path": ["Vibhaṅgapāḷi", "1. Suttantabhājanīyaṃ"],
            "start_sort_order": 1641, "end_sort_order": 1693,
        }
        children = [
            {"source_path": ["Vibhaṅgapāḷi", "1. Suttantabhājanīyaṃ", "1. Mettā"],
             "start_sort_order": 1642},
            {"source_path": ["Vibhaṅgapāḷi", "1. Suttantabhājanīyaṃ", "2. Karuṇā"],
             "start_sort_order": 1655},
        ]
        with patch("app.main.fetch_all", return_value=children):
            kept = _clip_overreaching_range(dict(parent))
        self.assertEqual(kept["end_sort_order"], 1693)
        self.assertNotIn("_readerRangeClipped", kept)

    def test_commentary_titles_are_read_through_the_vannana_suffix(self):
        """Chú giải đặt tên `X + vaṇṇanā`, đuôi đó che mất hậu tố thật của X.

        Trước bản sửa, KHÔNG mục nào của Chú giải/Phụ chú giải được nhận là đơn vị đọc
        (att 374 mục, tik 538). Ca khách báo: đoạn 235 nằm trong
        `4. Mettāsahagatasuttavaṇṇanā` (8 đoạn) nhưng trang đọc mở ra `6. Sākacchavaggo`
        (66 đoạn) kèm mục lục cả 6 bài chú giải của phẩm.
        """
        from app.main import _is_context_dependent_reader_title, _is_reader_unit_title

        # Bài chú giải của một bài kinh -> lớp DỨT KHOÁT.
        self.assertTrue(_is_reader_unit_title("4. Mettāsahagatasuttavaṇṇanā"))
        self.assertTrue(_is_reader_unit_title("2. Raṭṭhapālasuttavaṇṇanā"))
        self.assertFalse(_is_context_dependent_reader_title("4. Mettāsahagatasuttavaṇṇanā"))

        # `kathā + vaṇṇanā` -> vẫn là lớp phụ thuộc ngữ cảnh, để tiểu mục không thắng
        # chính bài chú giải chứa nó.
        self.assertTrue(_is_reader_unit_title("1. Suddhabrahmacariyakathāvaṇṇanā"))
        self.assertTrue(_is_context_dependent_reader_title("1. Suddhabrahmacariyakathāvaṇṇanā"))

        # Bỏ đuôi KHÔNG được kéo theo chương: chương thì vẫn là chương.
        for chapter in (
            "1. Bhūmivaggavaṇṇanā",
            "1. Paṭhamakaṇḍavaṇṇanā",
            "(6) 1. Puggalavaggavaṇṇanā",
            "2. Sīlakkhandhavaggavaṇṇanā",
        ):
            self.assertFalse(_is_reader_unit_title(chapter), chapter)

    def test_reader_unit_ignores_the_printed_ordinal_in_brackets(self):
        """Jātaka in kèm số thứ tự trong phẩm ở cuối tên, che mất đuôi `jatakam`."""
        from app.main import _is_reader_unit_title

        self.assertTrue(_is_reader_unit_title("543. Bhūridattajātakaṃ (6)"))
        self.assertTrue(_is_reader_unit_title("545. Mahānāradakassapajātakaṃ (8)"))
        self.assertTrue(_is_reader_unit_title("537. Mahāsutasomajātakaṃ (5)"))

        # Bỏ số trong ngoặc KHÔNG được kéo theo các chương vào: đuôi thật vẫn phải nằm
        # trong danh sách đã audit, nếu không thì "toàn bộ bài kinh" hoá ra cả một phẩm.
        self.assertFalse(_is_reader_unit_title("22. Mahānipāto (3)"))
        self.assertFalse(_is_reader_unit_title("1. Ekakanipāto (1)"))
        self.assertFalse(_is_reader_unit_title("4. Rājavaggo (2)"))
        # Chỉ bỏ ở CUỐI, và chỉ khi trong ngoặc thuần chữ số.
        self.assertFalse(_is_reader_unit_title("(6) 1. Puggalavaggo"))
        self.assertFalse(_is_reader_unit_title("3. Tikanipāta (ii)"))

        # Dạng NHIỀU CẤP `(nipāta-phẩm-vị trí)`: 264 mục Jātaka in kiểu này và từng trượt
        # bậc 1 vì bản sửa trước chỉ bỏ được `(6)`.
        self.assertTrue(_is_reader_unit_title("151. Rājovādajātakaṃ (2-1-1)"))
        self.assertTrue(_is_reader_unit_title("158. Suhanujātakaṃ (2-1-8)"))
        # Nới tới nhiều cấp KHÔNG được kéo chương vào.
        self.assertFalse(_is_reader_unit_title("2. Dukanipāto (2-1)"))

    def test_enumerated_landing_section_is_a_unit_not_a_fragment(self):
        """Bậc 2 không được leo khi mục gốc đã được người biên tập ĐÁNH SỐ.

        Ca khách báo: `13. Appamaññāvibhaṅgo -> 1. Suttantabhājanīyaṃ -> 2. Karuṇā`.
        `1. Suttantabhājanīyaṃ` (53 đoạn) chứa đúng bốn mục `1. Mettā`/`2. Karuṇā`/
        `3. Muditā`/`4. Upekkhā`, mỗi mục 13 đoạn - tứ vô lượng tâm. `2. Karuṇā` là đơn vị
        trọn vẹn, nhưng 13 < 20 nên bậc 2 leo lên và người đọc nhận cả bốn.
        """
        from app.main import _canonical_reader_section, _is_enumerated_title

        self.assertTrue(_is_enumerated_title("2. Karuṇā"))
        self.assertTrue(_is_enumerated_title("151. Rājovādajātakaṃ (2-1-1)"))
        self.assertFalse(_is_enumerated_title("Pubbenivāsapaṭisaṃyuttakathā"))
        self.assertFalse(_is_enumerated_title("Mahāvaggapāḷi"))

        karuna = {
            "id": "karuna", "document_id": "doc", "title": "2. Karuṇā",
            "source_path": ["Vibhaṅgapāḷi", "1. Suttantabhājanīyaṃ", "2. Karuṇā"],
            "start_sort_order": 1655, "end_sort_order": 1667,
        }
        parent = {
            "id": "parent", "document_id": "doc", "title": "1. Suttantabhājanīyaṃ",
            "source_path": ["Vibhaṅgapāḷi", "1. Suttantabhājanīyaṃ"],
            "start_sort_order": 1642, "end_sort_order": 1693,
        }

        with patch("app.main.fetch_all", return_value=[karuna, parent]):
            self.assertEqual(_canonical_reader_section(dict(karuna))["id"], "karuna")

        # Chiều ngược: mục KHÔNG số VÀ không mang hậu tố đơn vị nào thì vẫn phải leo, vì đó
        # mới là mẩu cắt giữa bài. Cố ý KHÔNG dùng tên đuôi `kathā` ở đây: `kathā` không số
        # nay là đơn vị lớp ngữ cảnh (xem `_is_reader_unit_row`), nên một fixture như vậy
        # sẽ kiểm sai thứ nó tưởng đang kiểm.
        scrap = {
            "id": "scrap", "document_id": "doc", "title": "Naārammaṇapaccayo",
            "source_path": ["Paṭṭhānapāḷi-3", "Naārammaṇapaccayo"],
            "start_sort_order": 4, "end_sort_order": 8,
        }
        parent = {
            "id": "parent", "document_id": "doc", "title": "10. Mahantaradukaṃ",
            "source_path": ["Paṭṭhānapāḷi-3"],
            "start_sort_order": 1, "end_sort_order": 300,
        }
        with patch("app.main.fetch_all", return_value=[scrap, parent]):
            self.assertEqual(_canonical_reader_section(dict(scrap))["id"], "parent")

    def test_falls_back_to_nearest_ancestor_when_no_title_qualifies(self):
        """Khách chốt: không có trọn bài kinh thì lấy phần gần nhất cũng được."""
        from app.main import _canonical_reader_section

        child = {
            "id": "c", "document_id": "d", "title": "Naārammaṇapaccayo",
            "source_path": ["Paṭṭhānapāḷi-3", "10. Mahantaradukaṃ", "Naārammaṇapaccayo"],
            "start_sort_order": 40, "end_sort_order": 44,
        }
        parent = {
            "id": "p", "document_id": "d", "title": "10. Mahantaradukaṃ",
            "source_path": ["Paṭṭhānapāḷi-3", "10. Mahantaradukaṃ"],
            "start_sort_order": 10, "end_sort_order": 300,
        }
        book = {
            "id": "b", "document_id": "d", "title": "Paṭṭhānapāḷi-3",
            "source_path": ["Paṭṭhānapāḷi-3"],
            "start_sort_order": 1, "end_sort_order": 9000,
        }

        with patch("app.main.fetch_all", return_value=[child, parent, book]):
            resolved = _canonical_reader_section(dict(child))

        # Không tiêu đề nào là bài kinh, nên leo lên tổ tiên GẦN NHẤT (291 đoạn), không
        # phải cả cuốn sách 9.000 đoạn.
        self.assertEqual(resolved["id"], "p")
        self.assertFalse(resolved["_readerUnitExact"])

    def test_fallback_keeps_a_subsection_that_is_already_substantial(self):
        """Ca uddāna Trường Bộ: leo vô điều kiện làm hỏng, không phải làm tốt.

        Câu kệ tóm tắt mở đầu tập nằm thẳng ở `Mahāvaggapāḷi` chứ không thuộc bài kinh
        nào, nên bậc 1 trượt là đúng. `Mahāvaggapāḷi` và `Dīghanikāyo` trùng khít nhau,
        nên leo lên không thêm một đoạn nội dung nào mà chỉ đổi tên hiển thị sang mục
        rộng hơn - người đọc tìm Mahāpadāna lại thấy tiêu đề "Dīghanikāyo".
        """
        from app.main import _canonical_reader_section

        vagga = {
            "id": "v", "document_id": "d", "title": "Mahāvaggapāḷi",
            "source_path": ["Suttapiṭaka", "Dīghanikāyo", "Mahāvaggapāḷi"],
            "start_sort_order": 1, "end_sort_order": 1361,
        }
        nikaya = {
            "id": "n", "document_id": "d", "title": "Dīghanikāyo",
            "source_path": ["Suttapiṭaka", "Dīghanikāyo"],
            "start_sort_order": 1, "end_sort_order": 1361,
        }

        with patch("app.main.fetch_all", return_value=[vagga, nikaya]):
            resolved = _canonical_reader_section(dict(vagga))

        self.assertEqual(resolved["title"], "Mahāvaggapāḷi")

    def test_fallback_refuses_an_ancestor_over_the_size_cap(self):
        from app.main import _canonical_reader_section, READER_FALLBACK_MAX_PASSAGES

        child = {
            "id": "c", "document_id": "d", "title": "Ekakanipātavaṇṇanā",
            "source_path": ["Jātakapāḷi-2", "22. Mahānipāto", "Ekakanipātavaṇṇanā"],
            "start_sort_order": 500, "end_sort_order": 510,
        }
        huge = {
            "id": "h", "document_id": "d", "title": "22. Mahānipāto",
            "source_path": ["Jātakapāḷi-2", "22. Mahānipāto"],
            "start_sort_order": 1,
            "end_sort_order": READER_FALLBACK_MAX_PASSAGES + 5000,
        }

        with patch("app.main.fetch_all", return_value=[child, huge]):
            resolved = _canonical_reader_section(dict(child))

        # Nguyên một nipāta không phải "bài kinh" và đổ vào một trang thì quá nặng;
        # thà giữ mục con còn hơn.
        self.assertEqual(resolved["id"], "c")
        self.assertFalse(resolved["_readerUnitExact"])

    def test_real_reader_unit_still_wins_over_the_fallback(self):
        from app.main import _canonical_reader_section

        child = {
            "id": "c", "document_id": "d", "title": "Kāyānupassanā",
            "source_path": ["Mahāpadānasuttaṃ", "Kāyānupassanā"],
            "start_sort_order": 40, "end_sort_order": 70,
        }
        noise = {
            "id": "noise", "document_id": "d", "title": "Pubbenivāsakathā",
            "source_path": ["Mahāpadānasuttaṃ", "Pubbenivāsakathā"],
            "start_sort_order": 35, "end_sort_order": 75,
        }
        sutta = {
            "id": "s", "document_id": "d", "title": "1. Mahāpadānasuttaṃ",
            "source_path": ["Mahāpadānasuttaṃ"],
            "start_sort_order": 4, "end_sort_order": 207,
        }

        # Mục nhiễu nhỏ hơn bài kinh, nhưng bài kinh phải thắng vì nó là đơn vị đọc thật.
        with patch("app.main.fetch_all", return_value=[child, noise, sutta]):
            resolved = _canonical_reader_section(dict(child))

        self.assertEqual(resolved["id"], "s")
        self.assertTrue(resolved["_readerUnitExact"])

    def test_katha_is_its_own_unit_when_the_only_ancestor_is_a_khandhaka(self):
        """Ca khách báo: `42. Sikkhāpadakathā` phải hiện riêng, không mở ra cả khandhaka.

        Số liệu thật từ `vin02m2`: mục này đúng 1 đoạn (`sort_order` 492), tổ tiên duy nhất
        là `1. Mahākhandhako` 616 đoạn - 234k ký tự Pāli, 76 lượt dịch AI, và bản ghép
        Indacanda chỉ phủ 255/616 đoạn. Trong khandhaka này KHÔNG có đơn vị nào nhỏ hơn để
        leo tới, nên `kathā` chính là nhãn gần nhất và phải được nhận.
        """
        from app.main import _canonical_reader_section

        katha = {
            "id": "k", "document_id": "d", "title": "42. Sikkhāpadakathā",
            "source_path": ["Mahāvaggapāḷi", "1. Mahākhandhako", "42. Sikkhāpadakathā"],
            "start_sort_order": 492, "end_sort_order": 492,
        }
        khandhaka = {
            "id": "kh", "document_id": "d", "title": "1. Mahākhandhako",
            "source_path": ["Mahāvaggapāḷi", "1. Mahākhandhako"],
            "start_sort_order": 0, "end_sort_order": 615,
        }

        with patch("app.main.fetch_all", return_value=[katha, khandhaka]):
            resolved = _canonical_reader_section(dict(katha))

        self.assertEqual(resolved["id"], "k")
        self.assertTrue(resolved["_readerUnitExact"])

    def test_katha_inside_a_sutta_still_climbs_to_the_sutta(self):
        """Chiều ngược lại, và đây là chiều dễ làm hỏng khi sửa chiều trên.

        Trong Trường Bộ, `kathā` là TIỂU MỤC của bài kinh. Nhận nó làm đơn vị đọc thì
        người đọc bấm "toàn bộ bài kinh" lại ra một mẩu - đúng lỗi khách từng báo trước
        đây. Bài kinh (lớp dứt khoát) phải thắng dù nó LỚN HƠN nhiều, tức không được chọn
        theo kích thước.
        """
        from app.main import _canonical_reader_section

        katha = {
            "id": "k", "document_id": "d", "title": "2. Sīlakkhandhakathā",
            "source_path": ["Sīlakkhandhavagga", "1. Brahmajālasuttaṃ", "2. Sīlakkhandhakathā"],
            "start_sort_order": 20, "end_sort_order": 24,
        }
        sutta = {
            "id": "s", "document_id": "d", "title": "1. Brahmajālasuttaṃ",
            "source_path": ["Sīlakkhandhavagga", "1. Brahmajālasuttaṃ"],
            "start_sort_order": 1, "end_sort_order": 200,
        }

        with patch("app.main.fetch_all", return_value=[katha, sutta]):
            resolved = _canonical_reader_section(dict(katha))

        self.assertEqual(resolved["id"], "s")
        self.assertTrue(resolved["_readerUnitExact"])

    @patch("app.main.official_translations_merged", return_value=[])
    @patch("app.main.fetch_all")
    @patch("app.main.fetch_one")
    def test_every_human_source_promotes_child_to_canonical_reader_range(
        self,
        fetch_one,
        fetch_all,
        _official_translations,
    ):
        child = {
            "id": "child",
            "document_id": "doc",
            "title": "A child heading",
            "source_path": ["Mahāpadānasuttaṃ", "A child heading"],
            "start_sort_order": 40,
            "end_sort_order": 70,
        }
        parent = {
            "id": "parent",
            "document_id": "doc",
            "title": "Mahāpadānasuttaṃ",
            "source_path": ["Mahāpadānasuttaṃ"],
            "start_sort_order": 4,
            "end_sort_order": 207,
        }
        fetch_one.return_value = child
        passages = [
            {
                "id": "p1",
                "sort_order": 40,
                "paragraph_no": "1",
                "xml_paragraph_no": "1",
                "pali_text": "Evaṃ me sutaṃ.",
                "hierarchy": {},
            }
        ]
        # Thứ tự truy vấn: (1) tổ tiên cho `_canonical_reader_section`, (2) cắt phạm vi ghi
        # quá rộng (`_clip_overreaching_range` - trả rỗng nghĩa là không có section lạ nào
        # nằm trong phạm vi, tức phạm vi đúng), (3) các đoạn, (4) mục lục. Bài kinh này chỉ
        # có một mục con nên mục lục rỗng - dưới 2 mục thì không hiện.
        fetch_all.side_effect = [[parent, child], [], passages, [child]]

        from app.main import _section_payload

        payload = _section_payload(
            "child",
            include_translation=False,
            language="en",
            source="sujato",
        )

        self.assertEqual(payload["sectionId"], "parent")
        self.assertEqual(payload["title"], "Mahāpadānasuttaṃ")
        self.assertEqual(payload["paliText"], "Passage 1\nEvaṃ me sutaṃ.")
        self.assertEqual(fetch_all.call_args_list[1].args[1], ["doc", 4, 207])
        # `paliText` vẫn là chuỗi phẳng cho bản dịch AI, còn `paragraphs` mang thêm neo.
        self.assertEqual(payload["paragraphs"][0]["anchor"], "doan-1")
        self.assertEqual(payload["paragraphs"][0]["passageIds"], ["p1"])
        self.assertEqual(payload["outline"], [])


class PitakaFilterTests(unittest.TestCase):
    def test_unknown_pitaka_never_reaches_the_sql_with_a_missing_parameter(self):
        """Giá trị Tạng lạ từng làm SẬP toàn bộ tìm kiếm, không phải lọc sai.

        `_pitaka_sql` sinh `like any(%s)` trong khi `PITAKA_PREFIXES.get()` ra rỗng nên
        tham số bị bỏ -> psycopg: "the query has 2 placeholders but 1 parameters were
        passed". `pitaka_type` là form field thường nên một trang cũ còn mở là đủ để sập.
        """
        from app.search_engine import PITAKA_PREFIXES, _pitaka_sql, resolve_pitaka_type

        for value in ("vinayapitaka", "Vinaya", "khong-ton-tai", "sutta-pitaka"):
            resolved = resolve_pitaka_type(value)
            self.assertIsNone(resolved, f"{value!r} phải về None thay vì cho qua")
            sql, params = _pitaka_sql(resolved)
            # Bất biến thật: số chỗ giữ %s phải bằng số tham số.
            self.assertEqual(sql.count("%s"), len(params))

        # Chiều ngược: giá trị UI thật KHÔNG được im lặng mất bộ lọc.
        for value in PITAKA_PREFIXES:
            self.assertEqual(resolve_pitaka_type(value), value)
            sql, params = _pitaka_sql(value)
            self.assertEqual(sql.count("%s"), len(params))
            self.assertEqual(params, PITAKA_PREFIXES[value])

        # `all`/rỗng vẫn nghĩa là không lọc, và khi đó SQL không có chỗ giữ nào.
        for value in (None, "", "all"):
            self.assertIsNone(resolve_pitaka_type(value))
        self.assertEqual(_pitaka_sql(None), ("", []))


class ReaderNavigationTests(unittest.TestCase):
    """Nhảy tới đoạn khớp + mục lục dựng từ cây section (không nhờ AI tóm tắt)."""

    @staticmethod
    def _passage(pid, sort_order, text, rend=None, para=None):
        return {
            "id": pid,
            "sort_order": sort_order,
            "paragraph_no": para,
            "xml_paragraph_no": para,
            "pali_text": text,
            "hierarchy": {"rend": rend} if rend else {},
        }

    def test_gatha_block_keeps_every_passage_id_it_swallowed(self):
        """Dòng kệ liền nhau gộp thành MỘT khối, nhưng phải giữ đủ id.

        Đây là chỗ nút nhảy dễ chết âm thầm: đoạn khớp tìm kiếm có thể là dòng kệ thứ ba
        trong khối. Nếu khối chỉ mang id dòng đầu thì tra không thấy và không nhảy đi đâu.
        """
        from app.main import GATHA_RENDS, _section_paragraphs

        rend = next(iter(GATHA_RENDS))
        rows = [
            self._passage("p1", 10, "Dòng văn xuôi."),
            self._passage("p2", 11, "Kệ dòng một.", rend=rend),
            self._passage("p3", 12, "Kệ dòng hai.", rend=rend),
            self._passage("p4", 13, "Văn xuôi tiếp."),
        ]
        blocks = _section_paragraphs(rows, "vi")

        self.assertEqual([b["anchor"] for b in blocks], ["doan-1", "doan-2", "doan-3"])
        self.assertEqual(blocks[1]["passageIds"], ["p2", "p3"])
        # Chuỗi phẳng phải y hệt bản cũ - nó là đầu vào chia chunk của bản dịch AI.
        self.assertEqual(
            "\n\n".join(b["text"] for b in blocks),
            "Dòng văn xuôi.\n\nKệ dòng một.\nKệ dòng hai.\n\nVăn xuôi tiếp.",
        )

    def test_outline_anchors_point_at_the_block_that_really_starts_the_part(self):
        """Neo theo `sort_order`, không theo thứ tự mục - đoạn rỗng làm lệch số khối."""
        from app.main import _outline_entries

        section = {
            "id": "unit", "document_id": "doc", "source_path": ["Khandhaka"],
            "start_sort_order": 0, "end_sort_order": 99,
        }
        children = [
            {"id": "a", "title": "1. Bodhikathā", "source_path": ["Khandhaka", "1. Bodhikathā"],
             "start_sort_order": 0, "end_sort_order": 17},
            {"id": "b", "title": "2. Ajapālakathā", "source_path": ["Khandhaka", "2. Ajapālakathā"],
             "start_sort_order": 18, "end_sort_order": 23},
        ]
        # Đoạn `sort_order` 1 rỗng nên bị bỏ; mục thứ hai vì thế bắt đầu ở khối 2, không
        # phải khối 3.
        rows = [
            self._passage("p1", 0, "Mở đầu."),
            self._passage("p2", 1, "   "),
            self._passage("p3", 18, "Tại cây Ajapāla."),
        ]
        from app.main import _section_paragraphs

        paragraphs = _section_paragraphs(rows, "vi")
        with patch("app.main.fetch_all", return_value=children):
            entries = _outline_entries(section, paragraphs)

        self.assertEqual([e["anchor"] for e in entries], ["doan-1", "doan-2"])
        self.assertEqual([e["title"] for e in entries], ["1. Bodhikathā", "2. Ajapālakathā"])
        self.assertEqual(entries[0]["passageCount"], 18)

    def test_wrapper_section_gets_an_outline_from_the_real_structure(self):
        """Section rác của bản import XML: phủ trọn tài liệu nhưng path nằm DƯỚI mục con thật.

        Các câu kệ uddāna trỏ `section_id` vào đúng mấy section này, nên trang đọc của chúng
        là cả bộ sách (`Suttanipātapāḷi` 3.892 đoạn). `_source_path_is_prefix` từ chối chúng
        là ĐÚNG, nên không có con cháu để dựng mục lục - phải lấy từ cấu trúc thật của tài
        liệu. Thuần bổ sung: không đổi một đoạn nào đang hiển thị.
        """
        from app.main import _section_outline

        wrapper = {
            "id": "wrap", "document_id": "doc",
            "title": "Khuddakanikāye",
            # Dấu hiệu của section rác: phủ cả sách mà path lại nằm trong phẩm đầu tiên.
            "source_path": ["Suttanipātapāḷi", "1. Uragavaggo", "Khuddakanikāye"],
            "start_sort_order": 0, "end_sort_order": 3891,
        }
        real = [
            {"id": "v1", "title": "1. Uragavaggo",
             "source_path": ["Suttanipātapāḷi", "1. Uragavaggo"],
             "start_sort_order": 90, "end_sort_order": 813},
            {"id": "v2", "title": "2. Cūḷavaggo",
             "source_path": ["Suttanipātapāḷi", "2. Cūḷavaggo"],
             "start_sort_order": 814, "end_sort_order": 1403},
            # Mục CHỒNG lên `2. Cūḷavaggo` - nhánh dự phòng phải bỏ nó, vì hai dòng mục lục
            # cùng trỏ vào một đoạn thì nút nhảy thành đoán.
            {"id": "dup", "title": "Trùng phạm vi",
             "source_path": ["Suttanipātapāḷi", "Trùng phạm vi"],
             "start_sort_order": 900, "end_sort_order": 1000},
        ]

        with patch("app.main.fetch_all", return_value=real):
            entries = _section_outline(dict(wrapper))

        self.assertEqual([row["id"] for row in entries], ["v1", "v2"])

    def test_verified_descendants_keep_overlapping_entries(self):
        """Chiều ngược: guard chồng lấn KHÔNG được áp cho con cháu đã xác thực prefix.

        Đo trên toàn kho, bật guard ở đó làm 19 trang bị cắt bớt dòng mục lục và 1 trang
        mất hẳn mục lục. Ở đó quan hệ cây là thật nên hai mục chồng nhau vẫn là hai mục
        thật, nút nhảy vẫn tới đúng đoạn của mình.
        """
        from app.main import _section_outline

        section = {
            "id": "unit", "document_id": "doc", "title": "5. Pācittiyakaṇḍaṃ",
            "source_path": ["Vinayapiṭaka", "5. Pācittiyakaṇḍaṃ"],
            "start_sort_order": 1, "end_sort_order": 1418,
        }
        # Phẩm và điều học đầu tiên cùng bắt đầu ở đoạn 1 - chồng nhau thật, và cả hai đều
        # là mục thật của cây.
        children = [
            {"id": "vagga", "title": "1. Musāvādavaggo",
             "source_path": ["Vinayapiṭaka", "5. Pācittiyakaṇḍaṃ", "1. Musāvādavaggo"],
             "start_sort_order": 1, "end_sort_order": 314},
            {"id": "rule", "title": "1. Musāvādasikkhāpadaṃ",
             "source_path": ["Vinayapiṭaka", "5. Pācittiyakaṇḍaṃ", "1. Musāvādasikkhāpadaṃ"],
             "start_sort_order": 1, "end_sort_order": 25},
        ]

        with patch("app.main.fetch_all", return_value=children):
            entries = _section_outline(dict(section))

        self.assertEqual({row["id"] for row in entries}, {"vagga", "rule"})

    def test_outline_labels_refuse_a_translation_with_the_wrong_line_count(self):
        """Nhãn lệch dòng dẫn người đọc tới đoạn nói chuyện khác - thà giữ tiêu đề Pāli."""
        from app.main import _outline_labels

        children = [
            {"id": "a", "title": "1. Bodhikathā", "source_path": ["K", "1. Bodhikathā"],
             "start_sort_order": 0, "end_sort_order": 17},
            {"id": "b", "title": "2. Ajapālakathā", "source_path": ["K", "2. Ajapālakathā"],
             "start_sort_order": 18, "end_sort_order": 23},
        ]
        section = {
            "id": "unit", "document_id": "doc", "source_path": ["K"],
            "start_sort_order": 0, "end_sort_order": 99,
        }

        with patch("app.main.fetch_all", return_value=children):
            with patch("app.main.translate_text_cached", return_value={"text": "Chỉ một dòng"}):
                self.assertEqual(_outline_labels(section, "vi"), {})

            with patch(
                "app.main.translate_text_cached",
                return_value={"text": "1. Câu chuyện giác ngộ\n2. Câu chuyện cây Ajapāla"},
            ):
                self.assertEqual(
                    _outline_labels(section, "vi"),
                    {
                        "1. Bodhikathā": "1. Câu chuyện giác ngộ",
                        "2. Ajapālakathā": "2. Câu chuyện cây Ajapāla",
                    },
                )


def _entry(source, text, whole=False, position=None):
    """Một dòng như `official_translations_for` trả về."""
    return {
        "source": source,
        "label": source,
        "text": text,
        "passageLevel": not whole,
        "position": position,
    }


class TranslationCoverageTests(unittest.TestCase):
    def test_official_source_order_puts_ai_last(self):
        from app.translation_sources import AI_SOURCE, SOURCE_ORDER

        # Khách chốt giữ nguyên 5 option riêng, hai dòng Indacanda đứng cạnh nhau.
        self.assertEqual(
            SOURCE_ORDER,
            ("indacanda", "indacanda_full", "minh_chau", "sujato", "brahmali", AI_SOURCE),
        )

    def test_labels_are_the_fixed_strings_the_client_asked_for(self):
        from app.translation_sources import source_label

        self.assertEqual(
            source_label("indacanda", "vi"),
            "Bản dịch của Tỳ Khưu Indacanda (trích đoạn ngắn)",
        )
        self.assertEqual(
            source_label("indacanda_full", "vi"),
            "Bản dịch của Tỳ Khưu Indacanda (toàn bộ bài kinh)",
        )

    @patch("app.translation_sources.official_translations_for")
    def test_both_indacanda_options_stay_separate(self, official_for):
        official_for.return_value = {
            "p1": [_entry("indacanda", "Đoạn một"), _entry("indacanda_full", "Trọn bài", whole=True)],
            "p2": [_entry("indacanda", "Đoạn hai")],
        }

        from app.translation_sources import official_translations_merged

        result = official_translations_merged(["p1", "p2"], "vi")
        by_source = {item["source"]: item for item in result}

        self.assertEqual(set(by_source), {"indacanda", "indacanda_full"})
        self.assertTrue(by_source["indacanda_full"]["wholeSutta"])
        self.assertFalse(by_source["indacanda"]["wholeSutta"])

    @patch("app.translation_sources.official_translations_for")
    def test_whole_option_is_built_from_passage_rows_when_no_whole_row_exists(self, official_for):
        """Mâu thuẫn khách chỉ ra: đã có trích đoạn thì phải đọc hết bài được.

         chỉ phủ 251 bài trên 30.590 dòng cấp đoạn, nên nếu option toàn
        bộ chỉ đọc đúng nguồn đó thì gần như bài nào cũng hiện "(chưa có dữ liệu)" ngay
        cạnh một option trích đoạn đang chạy tốt.
        """
        official_for.return_value = {"p1": [_entry("indacanda", "Một")], "p3": [_entry("indacanda", "Ba")]}

        from app.translation_sources import official_translations_merged

        # Trang đọc: option toàn bộ được dựng từ chính các dòng cấp đoạn.
        reader = {i["source"]: i for i in official_translations_merged(
            ["p1", "p2", "p3"], "vi", covers_whole_sutta=True)}
        self.assertIn("indacanda_full", reader)
        self.assertEqual(reader["indacanda_full"]["coveragePercent"], 67)
        # Ghép được 67% vẫn cho đọc - không đòi tròn 100%. Khách đã chốt bỏ mốc chen giữa
        # nội dung, nên mức phủ chỉ còn được nói qua con số ở tiêu đề: phải giữ bằng được.
        self.assertNotIn("chưa ghép", reader["indacanda_full"]["text"])
        self.assertEqual(reader["indacanda_full"]["missingPassages"], 1)

        # Trang kết quả KHÔNG dựng, vì ở đó chỉ có mấy đoạn của trích dẫn nên bản ghép
        # sẽ trùng khít option trích đoạn bên cạnh - hai dòng y hệt nhau.
        card = {i["source"]: i for i in official_translations_merged(["p1", "p2", "p3"], "vi")}
        self.assertNotIn("indacanda_full", card)

    def test_whole_option_is_offered_wherever_passage_rows_exist(self):
        from app.translation_sources import WHOLE_FALLBACK_SOURCE

        self.assertEqual(WHOLE_FALLBACK_SOURCE["indacanda_full"], "indacanda")

    @patch("app.translation_sources.official_translations_for")
    def test_passage_source_counts_the_missing_passages_without_marking_the_text(self, official_for):
        official_for.return_value = {
            "p1": [_entry("indacanda", "Một")],
            "p3": [_entry("indacanda", "Ba")],
        }

        from app.translation_sources import official_translations_merged

        result = official_translations_merged(["p1", "p2", "p3"], "vi")

        # Khách chốt bỏ mốc chen giữa nội dung, nên đoạn 2 hụt KHÔNG để lại dấu vết nào
        # trong văn bản - đo được là mốc cũ nói quá (xem docstring `_join_with_gap_markers`).
        self.assertEqual(result[0]["text"], "Một\n\nBa")
        # Nhưng phần đếm phải sống sót: đây là thứ duy nhất còn nói lên chỗ hụt.
        self.assertEqual(result[0]["missingPassages"], 1)
        self.assertEqual(result[0]["coverageCount"], 2)
        self.assertEqual(result[0]["coverageTotal"], 3)
        self.assertEqual(result[0]["coveragePercent"], 67)
        self.assertFalse(result[0]["complete"])

    @patch("app.translation_sources.official_translations_for")
    def test_missing_count_covers_both_ends_not_just_the_middle(self, official_for):
        official_for.return_value = {"p3": [_entry("sujato", "Giữa")]}

        from app.translation_sources import official_translations_merged

        result = official_translations_merged(["p1", "p2", "p3", "p4", "p5", "p6"], "en")

        # Không còn mốc nào trong văn bản.
        self.assertEqual(result[0]["text"], "Giữa")
        # Phần hụt Ở ĐẦU (2 đoạn) và Ở CUỐI (3 đoạn) vẫn phải vào số đếm, không chỉ phần
        # hụt kẹp giữa hai đoạn đã dịch - đếm sót hai đầu thì tỉ lệ phủ báo cao hơn thật.
        self.assertEqual(result[0]["missingPassages"], 5)
        self.assertEqual(result[0]["coveragePercent"], 17)


if __name__ == "__main__":
    unittest.main()
