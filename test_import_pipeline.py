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


class TranslationCoverageTests(unittest.TestCase):
    def test_official_source_order_puts_ai_last(self):
        from app.translation_sources import AI_SOURCE, SOURCE_ORDER

        self.assertEqual(
            SOURCE_ORDER,
            ("indacanda", "indacanda_full", "minh_chau", "sujato", "brahmali", AI_SOURCE),
        )

    @patch("app.translation_sources.official_translations_for")
    def test_passage_source_reports_partial_coverage(self, official_for):
        official_for.return_value = {
            "p1": [
                {
                    "source": "indacanda",
                    "label": "Indacanda",
                    "text": "Một",
                    "passageLevel": True,
                    "position": None,
                }
            ],
            "p3": [
                {
                    "source": "indacanda",
                    "label": "Indacanda",
                    "text": "Ba",
                    "passageLevel": True,
                    "position": None,
                }
            ],
        }

        from app.translation_sources import official_translations_merged

        result = official_translations_merged(["p1", "p2", "p3"], "vi")

        self.assertEqual(result[0]["text"], "Một\n\nBa")
        self.assertEqual(result[0]["coverageCount"], 2)
        self.assertEqual(result[0]["coverageTotal"], 3)
        self.assertEqual(result[0]["coveragePercent"], 67)
        self.assertFalse(result[0]["complete"])


if __name__ == "__main__":
    unittest.main()
