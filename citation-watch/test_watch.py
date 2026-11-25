#!/usr/bin/env python3
"""Tests for the parts of citation-watch that would fail quietly.

Run with: python -m unittest discover citation-watch

Network calls are not tested here. What is tested is the logic that would
produce a plausible-looking but wrong digest: a search phrase that silently
splits into two filters, an abstract reassembled in the wrong order, and a
link that points at nothing.
"""

import os
import unittest

import watch


class CleanPhrase(unittest.TestCase):
    def test_comma_would_split_the_filter_so_it_is_removed(self):
        # OpenAlex separates filters with commas. A phrase containing one
        # would become two filters and quietly change the query.
        self.assertEqual(
            watch.clean_phrase("speech, heart failure"), "speech heart failure"
        )

    def test_collapses_whitespace(self):
        self.assertEqual(watch.clean_phrase("  voice   biomarker\n"), "voice biomarker")


class AbstractFromIndex(unittest.TestCase):
    def test_words_come_back_in_position_order(self):
        index = {"failure": [1], "Heart": [0], "detected": [2]}
        self.assertEqual(watch.abstract_from_index(index), "Heart failure detected")

    def test_repeated_word_appears_at_each_position(self):
        index = {"the": [0, 2], "end": [3], "of": [1]}
        self.assertEqual(watch.abstract_from_index(index), "the of the end")

    def test_missing_or_malformed_index_gives_empty_string(self):
        self.assertEqual(watch.abstract_from_index(None), "")
        self.assertEqual(watch.abstract_from_index({}), "")
        self.assertEqual(watch.abstract_from_index({"x": "not-a-list"}), "")


class LinkFor(unittest.TestCase):
    def test_full_doi_url_is_used_as_is(self):
        work = {"doi": "https://doi.org/10.1121/1.5007092", "id": "https://openalex.org/W1"}
        self.assertEqual(watch.link_for(work), "https://doi.org/10.1121/1.5007092")

    def test_bare_doi_is_expanded(self):
        work = {"doi": "10.1121/1.5007092", "id": "https://openalex.org/W1"}
        self.assertEqual(watch.link_for(work), "https://doi.org/10.1121/1.5007092")

    def test_falls_back_to_openalex_when_there_is_no_doi(self):
        work = {"doi": None, "id": "https://openalex.org/W1"}
        self.assertEqual(watch.link_for(work), "https://openalex.org/W1")


class BuildUrl(unittest.TestCase):
    def setUp(self):
        self.original = os.environ.get("OPENALEX_MAILTO")
        os.environ.pop("OPENALEX_MAILTO", None)

    def tearDown(self):
        os.environ.pop("OPENALEX_MAILTO", None)
        if self.original is not None:
            os.environ["OPENALEX_MAILTO"] = self.original

    def test_no_mailto_is_sent_unless_the_env_var_is_set(self):
        url = watch.build_url(["cites:W1"], 25)
        self.assertNotIn("mailto", url)

    def test_mailto_is_sent_when_the_env_var_is_set(self):
        os.environ["OPENALEX_MAILTO"] = "someone@example.org"
        url = watch.build_url(["cites:W1"], 25)
        self.assertIn("mailto=someone%40example.org", url)

    def test_filters_are_comma_joined(self):
        url = watch.build_url(["cites:W1", "type:!preprint"], 5)
        self.assertIn("cites%3AW1%2Ctype%3A%21preprint", url)


class Byline(unittest.TestCase):
    def test_three_or_fewer_are_listed(self):
        self.assertEqual(watch.byline(["A", "B"]), "A, B")

    def test_more_than_three_are_truncated_with_a_count(self):
        self.assertEqual(
            watch.byline(["A", "B", "C", "D"]), "A, B, C, et al. (4 authors)"
        )

    def test_empty_names_are_dropped(self):
        self.assertEqual(watch.byline(["", ""]), "_authors not listed_")


class Snippet(unittest.TestCase):
    def test_short_text_is_untouched(self):
        self.assertEqual(watch.snippet("short abstract"), "short abstract")

    def test_long_text_is_cut_at_a_word_boundary(self):
        text = "word " * 100
        out = watch.snippet(text, limit=20)
        self.assertTrue(out.endswith(" ..."))
        self.assertLessEqual(len(out), 25)

    def test_absent_abstract_is_marked_rather_than_blank(self):
        self.assertEqual(watch.snippet(""), "_no abstract indexed_")


class TitleKey(unittest.TestCase):
    def test_same_paper_indexed_twice_collapses_to_one_key(self):
        # Seen in a real run: one paper held as both a landing page and a PDF,
        # with different OpenAlex ids, so id-only dedup let both through.
        a = "A Comparative Study of Data Leakage Effects in Financial Fraud Detection Systems"
        b = "A comparative study of data leakage effects in financial fraud detection systems."
        self.assertEqual(watch.title_key(a), watch.title_key(b))

    def test_different_papers_keep_different_keys(self):
        self.assertNotEqual(watch.title_key("Heart sounds"), watch.title_key("Heart rate"))

    def test_empty_title_gives_an_empty_key(self):
        self.assertEqual(watch.title_key(""), "")
        self.assertEqual(watch.title_key(None), "")


class QueriesFrom(unittest.TestCase):
    def test_topic_field_is_honoured(self):
        config = {"topics": [{"label": "L", "phrase": '"data leakage"', "field": "title"}]}
        self.assertEqual(
            watch.queries_from(config)[0]["filters"], ['title.search:"data leakage"']
        )

    def test_unknown_field_falls_back_rather_than_building_a_bad_filter(self):
        config = {"topics": [{"label": "L", "phrase": "x", "field": "nonsense"}]}
        self.assertEqual(
            watch.queries_from(config)[0]["filters"], ["title_and_abstract.search:x"]
        )

    def test_seed_becomes_a_cites_filter(self):
        config = {"seeds": [{"label": "GeMAPS", "id": "W1"}]}
        query = watch.queries_from(config)[0]
        self.assertEqual(query["filters"], ["cites:W1"])
        self.assertEqual(query["label"], "Cites: GeMAPS")


class Render(unittest.TestCase):
    def test_a_capped_query_says_so_instead_of_looking_complete(self):
        found = {
            "Topic": [
                {
                    "title": "T",
                    "url": "u",
                    "date": "2026-11-01",
                    "venue": "V",
                    "type": "article",
                    "cited_by": 0,
                    "authors": ["A"],
                    "abstract": "a",
                }
            ]
        }
        out = watch.render(found, "2026-11-25", [], {"Topic": 74})
        self.assertIn("74 matches in this window", out)

    def test_an_uncapped_query_carries_no_such_note(self):
        found = {"Topic": []}
        out = watch.render(found, "2026-11-25", [], {})
        self.assertNotIn("matches in this window", out)

    def test_failed_queries_are_named_in_the_digest(self):
        out = watch.render({}, "2026-11-25", ["Cites: GeMAPS"])
        self.assertIn("Cites: GeMAPS", out)
        self.assertIn("No new works", out)

    def test_appendix_does_not_repeat_the_title(self):
        found = {
            "Topic": [
                {
                    "title": "T",
                    "url": "u",
                    "date": "2026-11-01",
                    "venue": "V",
                    "type": "article",
                    "cited_by": 0,
                    "authors": ["A"],
                    "abstract": "a",
                }
            ]
        }
        out = watch.render_appendix(found)
        self.assertNotIn("# Citation watch", out)
        self.assertIn("Added by a later run", out)


if __name__ == "__main__":
    unittest.main()
