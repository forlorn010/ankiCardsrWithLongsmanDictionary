# Longman → Anki Card Generator

[English](README.md) | [Русский](README.ru.md)

A small desktop application that turns a list of English words into
Anki-ready flashcards. It takes the first example sentence from the
[Longman Dictionary of Contemporary English](https://www.ldoceonline.com/),
replaces the target word with `_____`, and exports a tab-separated text file
with four fields: **Front**, **Word**, **Back**, and **Audio**.

The app is written in Python with Tkinter and has **no third-party
dependencies** — no `pip install` is needed.

---

## Features

- Paste many words at once — one per line, or separated by commas/semicolons.
- Filter by part of speech: noun, verb, adjective, adverb, phrasal verb, etc.
- Uses Longman's first usable example sentence for the selected entry (the
  first one that actually contains the target word).
- Handles inflected forms (`colors`, `ran`, `studied`, `lighter`, phrases such
  as `take off` and `take-off`).
- Understands definitions like **“the American spelling of colour”**:
  if the American page has no examples, the British page is fetched
  automatically, while the card still links to the American word.
- Keeps the word count, progress, and per-word errors visible in the UI.
- Runs lookups in the background, so the window stays responsive.
- Writes a UTF-8 Anki import file with clickable Longman links.
- Exports the **Audio** field empty, as requested.

---

## Requirements

- **Python 3.10 or newer**
- **Tkinter** (included with the standard Windows and macOS installers;
  on Linux install the `python3-tk` package)
- An internet connection to `ldoceonline.com`

No external Python packages are required.

---

## Quick start

```bash
python app.py
```

On Windows you can also use:

```bash
py -3 app.py
```

The window opens with an empty word list. Cards are saved by default to:

```text
output/longman_cards_YYYYMMDD_HHMMSS.txt
```

---

## How to use

### 1. Enter words

Use the large text area. Any of these formats work:

```text
color
beautiful
take off
```

```text
apple, run; beautiful
```

- New lines, commas, and semicolons separate words.
- **Spaces are not separators**, so multi-word entries such as `take off`
  stay together.
- Duplicate words are ignored (case-insensitive).
- Leading list numbering such as `1.` or `2)` is removed automatically.

### 2. Choose a part of speech

Use the **Part of speech** dropdown:

- `Any` — use the first entry that has an example.
- `noun`, `verb`, `adjective`, `adverb`, `phrasal verb`, etc. — restrict the
  search to that entry type.

The filter is applied both to the Longman entry and to the British fallback
page when an American spelling is involved.

### 3. Generate

Click **Generate cards** (or press `Ctrl+Enter`).

The progress panel shows every successful card and every skipped word with a
reason. If some words fail, the rest are still exported. Use **Stop** to finish
after the current word; already generated cards are saved.

### 4. Import into Anki

1. In Anki, create a note type with exactly these four fields, in this order:

   | Field | Content |
   |---|---|
   | `Front` | Example sentence with `_____` |
   | `Word` | The target word |
   | `Back` | Clickable link to the Longman page |
   | `Audio` | Always empty |

2. Choose **File → Import…** and select the generated `.txt` file.
3. Make sure the separator is **Tab** and **Allow HTML in fields** is enabled.
4. Map the columns to the fields above and import.

The file starts with Anki import headers:

```text
#separator:tab
#html:true
#deck:Longman Dictionary
#columns:Front	Word	Back	Audio
```

Recent Anki versions use `#columns` to map the fields automatically. Older
versions treat those lines as comments and will ask you to map the fields
manually.

The `Back` field contains an HTML link, for example:

```html
<a href="https://www.ldoceonline.com/dictionary/color">https://www.ldoceonline.com/dictionary/color</a>
```

---

## Example output

For the input `color` with part of speech `noun`, a row looks like this
(columns separated by tabs):

| Front | Word | Back | Audio |
|---|---|---|---|
| These are nothing but elitist attempts at separating classes and _____ and keeping the poor where the wealthy have put them. | color | link to `…/dictionary/color` | *(empty)* |

---

## How the scraper works

1. The word is converted to a Longman URL slug:
   `color` → `https://www.ldoceonline.com/dictionary/color`,
   `take off` → `https://www.ldoceonline.com/dictionary/take-off`.
2. The page is parsed with Python's built-in `html.parser` — no browser
   automation is used.
3. The parser follows the main dictionary column and the
   `span.dictentry → span.assetlink → span.exaGroup` structure.
4. The correct entry is selected using the requested part of speech.
5. The first example in the entry's main `exaGroup` that contains the target
   word is extracted. Normally this is Longman's first line; the next line is
   used only when the first one contains just a derived form (for example,
   `walkers` for the noun `walk`).
6. Occurrences marked by Longman (`span.NodeW`) and common inflections are
   replaced with `_____`.
7. For definitions of the form **“the American spelling of [British word]”**:
   - if the American page has an example, that example is used;
   - otherwise the British word's page is fetched and its first example is
     used, with the British spelling blanked as well.
8. The `Back` field always links to the dictionary page for the word the user
   entered.

Requests are polite: there is a short delay between page loads, responses are
cached for the session, and failed requests are retried a few times.

---

## Limitations and notes

- Longman can change its HTML at any time; the parser follows the structure
  described above.
- Some words have no example in the main example group. Those words are
  reported as skipped instead of producing a bad card.
- The first example is sometimes a dialogue or a fragment. It is still the
  first example Longman provides.
- Network errors, rate limiting, or regional blocking can cause lookups to
  fail. The app shows the reason for each skipped word.
- This tool is intended for personal/educational use. Respect Longman's terms
  of service and do not redistribute scraped content.

---

## Troubleshooting

| Problem | Solution |
|---|---|
| `ModuleNotFoundError: No module named 'tkinter'` | Install Tkinter (`sudo apt install python3-tk` on Debian/Ubuntu, or use the standard Windows/macOS Python installer). |
| “Could not reach the Longman Dictionary” | Check the internet connection and try again; Longman occasionally blocks or times out requests. |
| “has no … entry” | Choose `Any`, or select a part of speech listed in the message. |
| “No example sentence was found” | Longman has no corpus example for that entry; try another part of speech or skip the word. |
| The file exists but Anki shows one column | Enable tab separation and HTML in Anki's import dialog, then map the four fields. |
| Some words are skipped | Read the red lines in the progress panel; each one explains why. |

---

## Project structure

```text
.
├── app.py            # Tkinter interface, input parsing, background worker
├── longman.py        # HTML parser, Longman client, example extraction, blanking
├── anki_export.py    # Anki TSV format and UTF-8 file writer
├── tests/
│   └── test_core.py  # parser, fallback, inflection, and export tests
├── output/           # generated .txt files (created automatically)
├── README.md         # this file
└── README.ru.md      # Russian documentation
```

---

## Tests

Run the standard-library test suite:

```bash
python -m unittest discover -s tests -v
```

The tests use small synthetic HTML pages, so they do not need internet access.
