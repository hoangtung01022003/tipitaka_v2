import unittest
from unittest.mock import patch

from import_indacanda import split_verses
from import_sujato import merged_segment_text


class IndacandaPdfParsingTests(unittest.TestCase):
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


class SujatoCommentTests(unittest.TestCase):
    def test_comment_is_inserted_inline_without_an_added_label(self):
        text = merged_segment_text(
            ["dn14:1.10.1"],
            {"dn14:1.10.1": "The translated sentence."},
            {"dn14:1.10.1": "<i>Small text</i> &amp; detail."},
        )

        self.assertEqual(text, "The translated sentence.\nSmall text & detail.")
        self.assertNotIn("note", text.lower())


class WholeSuttaReaderTests(unittest.TestCase):
    @patch("app.main.official_translations_merged", return_value=[])
    @patch("app.main.fetch_all")
    @patch("app.main.fetch_one")
    def test_whole_source_promotes_child_to_parent_range(
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
        fetch_one.side_effect = [child, parent]
        fetch_all.return_value = [
            {
                "id": "p1",
                "paragraph_no": "1",
                "xml_paragraph_no": "1",
                "pali_text": "Evaṃ me sutaṃ.",
                "hierarchy": {},
            }
        ]

        from app.main import _section_payload

        payload = _section_payload(
            "child",
            include_translation=False,
            language="en",
            source="indacanda_full",
        )

        self.assertEqual(payload["sectionId"], "parent")
        self.assertEqual(payload["title"], "Mahāpadānasuttaṃ")
        self.assertEqual(payload["paliText"], "Passage 1\nEvaṃ me sutaṃ.")
        self.assertEqual(fetch_all.call_args.args[1], ["doc", 4, 207])


if __name__ == "__main__":
    unittest.main()
