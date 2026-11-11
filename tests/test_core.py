from __future__ import annotations

import unittest

from longman import (
    Card,
    LongmanDictionary,
    NoExampleError,
    PartOfSpeechError,
    blank_word,
    parse_entry_page,
)
from anki_export import render_anki_text


def entry_html(headword: str, pos: str, examples: list[str],
               homnum: str = "", definition: str = "",
               section_intro: str = "") -> str:
    intro = (
        f'<span class="dictionary_intro span">{section_intro}</span>'
        if section_intro else ""
    )
    def_html = (
        f'<span class="Sense"><span class="DEF">{definition}</span></span>'
        if definition else ""
    )
    example_html = ""
    if examples:
        example_html = (
            '<span class="assetlink">'
            '<span class="exaGroup cexa1 exaGroup">'
            f'<span class="title">{headword}</span>'
            + "".join(
                '<span class="cexa1g1 exa"><span class="neutral span">• </span>'
                f"{example}</span>"
                for example in examples
            )
            + "</span></span>"
        )
    return (
        '<span class="dictentry">'
        f"{intro}"
        '<span class="ldoceEntry Entry"><span class="Head">'
        f'<span class="HWD">{headword}</span>'
        f'<span class="HOMNUM">{homnum}</span>'
        f'<span class="POS"> {pos}</span>'
        "</span>"
        f"{def_html}"
        "</span>"
        f"{example_html}"
        "</span>"
    )


def page_html(entries: list[str]) -> str:
    return (
        '<html><body class="page"><div class="content">'
        '<div class="responsive_cell6"><div class="entry_content">'
        '<div class="dictionary">'
        + "".join(entries)
        + "</div></div></div></div></body></html>"
    )


class FakeDictionary(LongmanDictionary):
    def __init__(self, pages: dict[str, str]) -> None:
        super().__init__(delay=0)
        self.pages = pages

    def fetch(self, url: str) -> tuple[str, str]:
        if url not in self.pages:
            raise AssertionError(f"Unexpected URL fetched: {url}")
        return self.pages[url], url


class ParsingTests(unittest.TestCase):
    def test_parses_main_entries_and_examples(self) -> None:
        html = page_html([
            entry_html("color", "noun, verb", ["The <span class=\"NodeW\">colors</span> are bright."]),
            entry_html("color", "verb", ["She likes to <span class=\"NodeW\">color</span>."], homnum="2"),
        ])
        page = parse_entry_page(html, "https://example.test/color", "color")
        self.assertEqual(len(page.entries), 2)
        self.assertEqual(page.entries[0].pos_list, ["noun", "verb"])
        self.assertEqual(page.entries[1].pos_list, ["verb"])
        self.assertEqual(len(page.entries[0].groups), 1)

    def test_only_first_dictionary_section_is_used(self) -> None:
        html = page_html([
            entry_html("light", "noun", ["A <span class=\"NodeW\">light</span> shone."]),
            entry_html("light", "noun", ["Business example."],
                       section_intro="From Longman Business Dictionary"),
        ])
        page = parse_entry_page(html, "https://example.test/light", "light")
        self.assertEqual(len(page.entries), 1)

    def test_pos_selection_prefers_specific_entry(self) -> None:
        html = page_html([
            entry_html("color", "noun, verb", ["The <span class=\"NodeW\">colors</span> are bright."]),
            entry_html("color", "verb", ["She likes to <span class=\"NodeW\">color</span>."], homnum="2"),
        ])
        page = parse_entry_page(html, "https://example.test/color", "color")
        noun = page.entries_for("noun")
        verb = page.entries_for("verb")
        self.assertEqual([entry.pos_list for entry in noun], [["noun", "verb"]])
        self.assertEqual([entry.pos_list for entry in verb], [["verb"]])

    def test_phrasal_verb_headword_has_no_hwd_span(self) -> None:
        html = page_html([
            '<span class="dictentry"><span class="dictionary_intro span">'
            "From Longman Dictionary of Contemporary English</span>"
            '<span class="ldoceEntry Entry"><span class="Head">'
            '<span class="PHRVBHWD">take off</span>'
            '<span class="POS"> phrasal verb</span></span></span>'
            '<span class="assetlink"><span class="exaGroup cexa1 exaGroup">'
            '<span class="title">take off</span>'
            '<span class="cexa1g1 exa"><span class="neutral span">• </span>'
            'Can I <span class="NodeW">take it off</span>?</span>'
            "</span></span></span>",
        ])
        page = parse_entry_page(html, "https://example.test/take-off", "take off")
        self.assertEqual(len(page.entries), 1)
        self.assertEqual(page.entries[0].headword, "take off")
        self.assertEqual(page.entries[0].pos_list, ["phrasal verb"])

    def test_skips_first_example_without_the_target_word(self) -> None:
        html = page_html([
            entry_html(
                "walk", "noun",
                [
                    "The park was full of Sunday afternoon walkers.",
                    "We went for a <span class=\"NodeW\">walk</span>.",
                ],
            ),
        ])
        page = parse_entry_page(html, "https://example.test/walk", "walk")
        found = LongmanDictionary._find_example(page.entries, "walk")
        self.assertIsNotNone(found)
        assert found is not None
        self.assertEqual(found[4], "We went for a _____.")


class SlugTests(unittest.TestCase):
    def test_slug(self) -> None:
        from longman import word_to_slug

        self.assertEqual(word_to_slug("take off"), "take-off")
        self.assertEqual(word_to_slug("can't"), "can-t")
        self.assertEqual(word_to_slug("Color"), "color")


class BlankingTests(unittest.TestCase):
    def test_blank_regular_and_marked_forms(self) -> None:
        self.assertEqual(
            blank_word("The colors are bright.", "color", ["colors"]),
            "The _____ are bright.",
        )
        self.assertEqual(
            blank_word("She studied all night.", "study"),
            "She _____ all night.",
        )
        self.assertEqual(
            blank_word("Camilli scored 936 runs.", "run", ["runs"]),
            "Camilli scored 936 _____.",
        )

    def test_blank_phrase_with_irregular_verb(self) -> None:
        self.assertEqual(
            blank_word("She took off her coat.", "take off"),
            "She _____ her coat.",
        )
        self.assertEqual(
            blank_word("The plane left at take-off.", "take off", ["take-off"]),
            "The plane left at _____.",
        )

    def test_blank_plural_phrase(self) -> None:
        self.assertEqual(
            blank_word("Many human beings live here.", "human being"),
            "Many _____ live here.",
        )

    def test_returns_none_when_word_is_missing(self) -> None:
        self.assertIsNone(blank_word("Nothing relevant here.", "color"))


class AmericanSpellingTests(unittest.TestCase):
    def test_uses_british_examples_when_american_page_has_none(self) -> None:
        american = page_html([
            entry_html(
                "anemia", "noun", [],
                definition='the American spelling of <span class="REFHWD">anaemia</span>',
            ),
        ])
        british = page_html([
            entry_html(
                "anaemia", "noun",
                ["Iron deficiency <span class=\"NodeW\">anaemia</span> was widespread."],
            ),
        ])
        client = FakeDictionary({
            "https://www.ldoceonline.com/dictionary/anemia": american,
            "https://www.ldoceonline.com/dictionary/anaemia": british,
        })
        card = client.lookup("anemia", "noun")
        self.assertEqual(card.front, "Iron deficiency _____ was widespread.")
        self.assertEqual(card.word, "anemia")
        self.assertEqual(card.american_of, "anaemia")
        self.assertEqual(card.url, "https://www.ldoceonline.com/dictionary/anemia")

    def test_uses_american_page_examples_when_present(self) -> None:
        american = page_html([
            entry_html(
                "color", "noun", ["The <span class=\"NodeW\">colors</span> are bright."],
                definition='the American spelling of <span class="REFHWD">colour</span>',
            ),
        ])
        client = FakeDictionary({
            "https://www.ldoceonline.com/dictionary/color": american,
        })
        card = client.lookup("color", "noun")
        self.assertEqual(card.front, "The _____ are bright.")
        self.assertEqual(card.example_url, "https://www.ldoceonline.com/dictionary/color")

    def test_missing_pos_on_both_pages(self) -> None:
        american = page_html([entry_html("color", "noun", [])])
        client = FakeDictionary({
            "https://www.ldoceonline.com/dictionary/color": american,
        })
        with self.assertRaises(PartOfSpeechError):
            client.lookup("color", "verb")

    def test_american_fallback_without_examples(self) -> None:
        american = page_html([
            entry_html(
                "color", "noun", [],
                definition='the American spelling of <span class="REFHWD">colour</span>',
            ),
        ])
        british = page_html([entry_html("colour", "noun", [])])
        client = FakeDictionary({
            "https://www.ldoceonline.com/dictionary/color": american,
            "https://www.ldoceonline.com/dictionary/colour": british,
        })
        with self.assertRaises(NoExampleError):
            client.lookup("color", "noun")


class ExportTests(unittest.TestCase):
    def test_export_headers_and_empty_audio_field(self) -> None:
        cards = [Card(
            word="color & shade",
            front='The <colors> are "bright".',
            url="https://www.ldoceonline.com/dictionary/color?a=1&b=2",
        )]
        text = render_anki_text(cards)
        lines = text.splitlines()
        self.assertEqual(lines[0], "#separator:tab")
        self.assertEqual(lines[1], "#html:true")
        self.assertEqual(lines[3], "#columns:Front\tWord\tBack\tAudio")
        row = lines[4]
        self.assertTrue(row.endswith("\t"))
        self.assertIn("color &amp; shade", row)
        self.assertIn("The &lt;colors&gt; are", row)
        self.assertIn('href="https://www.ldoceonline.com/dictionary/color?a=1&amp;b=2"', row)
        self.assertEqual(row.count("\t"), 3)


if __name__ == "__main__":
    unittest.main()
