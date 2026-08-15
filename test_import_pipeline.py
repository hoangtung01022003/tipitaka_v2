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
                "paragraph_no": "1",
                "xml_paragraph_no": "1",
                "pali_text": "Evaṃ me sutaṃ.",
                "hierarchy": {},
            }
        ]
        fetch_all.side_effect = [[parent, child], passages]

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


def _entry(source, text, whole=False, position=None):
    """Một dòng như `official_translations_for` trả về."""
    from app.translation_sources import SOURCE_TO_TRANSLATOR

    return {
        "source": source,
        "translator": SOURCE_TO_TRANSLATOR.get(source, source),
        "label": source,
        "text": text,
        "passageLevel": not whole,
        "position": position,
    }


class TranslationCoverageTests(unittest.TestCase):
    def test_official_source_order_puts_ai_last(self):
        from app.translation_sources import AI_SOURCE, SOURCE_ORDER

        # `indacanda_full` KHÔNG còn là một mục riêng ở giao diện: nó là hình dạng thứ
        # hai của cùng dịch giả `indacanda`, gộp lại trong `TRANSLATOR_SOURCES`.
        self.assertEqual(
            SOURCE_ORDER,
            ("indacanda", "minh_chau", "sujato", "brahmali", AI_SOURCE),
        )

    def test_legacy_indacanda_full_maps_back_to_the_translator(self):
        from app.translation_sources import normalize_source

        # Trang đang mở sẵn trong trình duyệt và bookmark cũ còn gửi giá trị này; rơi về
        # AI thì người đọc bấm nút cũ lại thấy bản dịch máy thay cho bản của dịch giả.
        self.assertEqual(normalize_source("indacanda_full"), "indacanda")
        self.assertEqual(normalize_source("indacanda"), "indacanda")
        self.assertEqual(normalize_source("khong-co-that"), "ai")

    def test_label_states_the_shape_for_every_translator(self):
        from app.translation_sources import EXCERPT_SHAPE, WHOLE_SHAPE, source_label

        self.assertEqual(
            source_label("indacanda", "vi", WHOLE_SHAPE),
            "Bản dịch của Tỳ Khưu Indacanda (toàn bộ bài kinh)",
        )
        self.assertEqual(
            source_label("indacanda", "vi", EXCERPT_SHAPE),
            "Bản dịch của Tỳ Khưu Indacanda (trích đoạn ngắn trong bài kinh)",
        )
        # Khách yêu cầu áp cho cả bản tiếng Anh, không riêng Indacanda.
        self.assertTrue(
            source_label("sujato", "en", EXCERPT_SHAPE).endswith(" (short excerpt from the discourse)")
        )
        # Riêng AI giữ nguyên lối chia theo đoạn nên không mang hậu tố nào.
        self.assertEqual(source_label("ai", "vi", WHOLE_SHAPE), source_label("ai", "vi"))
        # Không biết hình dạng thì trả tên trần, dùng cho dropdown chọn nguồn.
        self.assertEqual(source_label("indacanda", "vi"), "Bản dịch của Tỳ Khưu Indacanda")

    @patch("app.translation_sources.official_translations_for")
    def test_two_indacanda_shapes_collapse_into_one_option(self, official_for):
        # Đúng cảnh khách báo: cùng một bài vừa có bản trọn bài vừa có bản cấp đoạn.
        official_for.return_value = {
            "p1": [_entry("indacanda", "Đoạn một"), _entry("indacanda_full", "Trọn bài", whole=True)],
            "p2": [_entry("indacanda", "Đoạn hai")],
        }

        from app.translation_sources import official_translations_merged

        result = official_translations_merged(["p1", "p2"], "vi")

        self.assertEqual(len(result), 1, "một dịch giả chỉ được hiện một lần")
        self.assertEqual(result[0]["source"], "indacanda")
        self.assertTrue(result[0]["wholeSutta"])
        self.assertEqual(result[0]["text"], "Trọn bài")
        self.assertIn("(toàn bộ bài kinh)", result[0]["label"])

    @patch("app.translation_sources.official_translations_for")
    def test_translator_falls_back_to_passage_shape(self, official_for):
        official_for.return_value = {"p1": [_entry("indacanda", "Đoạn một")]}

        from app.translation_sources import official_translations_merged

        result = official_translations_merged(["p1"], "vi")

        # Chưa có bản trọn bài thì vẫn phải dùng được, không hiện "(chưa có dữ liệu)".
        self.assertEqual(result[0]["source"], "indacanda")
        self.assertFalse(result[0]["wholeSutta"])
        self.assertIn("(trích đoạn ngắn trong bài kinh)", result[0]["label"])

    @patch("app.translation_sources.official_translations_for")
    def test_passage_source_marks_where_the_translation_is_missing(self, official_for):
        official_for.return_value = {
            "p1": [_entry("indacanda", "Một")],
            "p3": [_entry("indacanda", "Ba")],
        }

        from app.translation_sources import official_translations_merged

        result = official_translations_merged(["p1", "p2", "p3"], "vi")

        # Nối thẳng "Một\n\nBa" khiến người đọc tưởng mạch văn liên tục; mốc phải nằm
        # đúng chỗ đoạn 2 bị hụt.
        self.assertEqual(result[0]["text"], "Một\n\n[… thiếu 1 đoạn chưa có bản dịch …]\n\nBa")
        self.assertEqual(result[0]["missingPassages"], 1)
        self.assertEqual(result[0]["coverageCount"], 2)
        self.assertEqual(result[0]["coverageTotal"], 3)
        self.assertEqual(result[0]["coveragePercent"], 67)
        self.assertFalse(result[0]["complete"])

    @patch("app.translation_sources.official_translations_for")
    def test_complete_passage_assembly_is_labelled_whole_on_the_reader_page(self, official_for):
        official_for.return_value = {
            "p1": [_entry("sujato", "One")],
            "p2": [_entry("sujato", "Two")],
        }

        from app.translation_sources import official_translations_merged

        # Trang đọc truyền TOÀN BỘ đoạn của bài; phủ đủ 100% thì thứ hiện ra là trọn bài.
        reader = official_translations_merged(["p1", "p2"], "vi", covers_whole_sutta=True)
        self.assertIn("(toàn bộ bài kinh)", reader[0]["label"])
        self.assertEqual(reader[0]["text"], "One\n\nTwo")
        # `wholeSutta` vẫn False: nó nói về HÌNH DẠNG LƯU TRỮ, và trang đọc dựa vào nó để
        # in "ghép từ N/N đoạn" thay vì "bản dịch trọn bài của dịch giả".
        self.assertFalse(reader[0]["wholeSutta"])

        # Trang kết quả truyền vài đoạn của trích dẫn - phủ đủ chúng KHÔNG phải phủ đủ bài.
        card = official_translations_merged(["p1", "p2"], "vi")
        self.assertIn("(trích đoạn ngắn trong bài kinh)", card[0]["label"])

    @patch("app.translation_sources.official_translations_for")
    def test_incomplete_assembly_stays_an_excerpt_even_on_the_reader_page(self, official_for):
        official_for.return_value = {"p1": [_entry("sujato", "One")]}

        from app.translation_sources import official_translations_merged

        result = official_translations_merged(["p1", "p2"], "vi", covers_whole_sutta=True)
        self.assertIn("(trích đoạn ngắn trong bài kinh)", result[0]["label"])
        self.assertEqual(result[0]["missingPassages"], 1)

    @patch("app.translation_sources.official_translations_for")
    def test_gap_markers_merge_and_cover_both_ends(self, official_for):
        official_for.return_value = {"p3": [_entry("sujato", "Giữa")]}

        from app.translation_sources import official_translations_merged

        result = official_translations_merged(["p1", "p2", "p3", "p4", "p5", "p6"], "en")

        # Hai đoạn hụt liền nhau gộp thành MỘT mốc, không phải hai; và phần hụt ở đầu
        # lẫn ở cuối đều phải được nói ra.
        self.assertEqual(
            result[0]["text"],
            "[… 2 passage(s) not yet translated …]\n\nGiữa\n\n[… 3 passage(s) not yet translated …]",
        )
        self.assertEqual(result[0]["missingPassages"], 5)


if __name__ == "__main__":
    unittest.main()
