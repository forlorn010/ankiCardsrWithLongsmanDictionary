"""Longman Dictionary → Anki card generator (Tkinter UI).

Run with:
    python app.py

Only the Python standard library is required.
"""

from __future__ import annotations

import os
import queue
import re
import subprocess
import sys
import threading
from datetime import datetime
from pathlib import Path
import tkinter as tk
from tkinter import filedialog, messagebox, ttk
from tkinter.scrolledtext import ScrolledText

from anki_export import ensure_txt_suffix, write_anki_file
from longman import Card, LongmanDictionary, LongmanError

APP_TITLE = "Longman → Anki Card Generator"
APP_DIR = Path(__file__).resolve().parent
OUTPUT_DIR = APP_DIR / "output"

POS_OPTIONS = [
    "Any",
    "noun",
    "verb",
    "adjective",
    "adverb",
    "phrasal verb",
    "preposition",
    "conjunction",
    "pronoun",
    "determiner",
    "number",
    "interjection",
    "exclamation",
    "modal verb",
    "auxiliary verb",
]


def parse_word_list(text: str) -> list[str]:
    """Split the input on commas, semicolons, tabs and new lines.

    Spaces are intentionally *not* separators, so phrasal verbs such as
    "take off" can be entered on their own line.
    """
    words: list[str] = []
    seen: set[str] = set()
    for chunk in re.split(r"[\n\r,;]+", text):
        word = chunk.strip().strip("\"'")
        word = re.sub(r"^\s*(?:\d+[.)]|[-*•])\s+", "", word)
        word = re.sub(r"\s+", " ", word).strip()
        if not word:
            continue
        key = word.casefold()
        if key in seen:
            continue
        seen.add(key)
        words.append(word)
    return words


def open_path(path: Path) -> None:
    """Open a file with the operating system's default application."""
    if os.name == "nt":
        os.startfile(str(path))  # type: ignore[attr-defined]
    elif sys.platform == "darwin":
        subprocess.Popen(["open", str(path)])
    else:
        subprocess.Popen(["xdg-open", str(path)])


class CardGeneratorApp:
    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        self.queue: queue.Queue[tuple] = queue.Queue()
        self.stop_event = threading.Event()
        self.worker: threading.Thread | None = None
        self.last_output: Path | None = None
        self.running = False

        self.root.title(APP_TITLE)
        self.root.geometry("940x760")
        self.root.minsize(780, 620)
        self._configure_style()
        self._build_ui()
        self._update_word_count()
        self.root.after(90, self._poll_queue)
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)
        self.root.bind("<Control-Return>", lambda _event: self.start_generation())

    # -- UI construction ------------------------------------------------

    def _configure_style(self) -> None:
        style = ttk.Style(self.root)
        themes = style.theme_names()
        if "vista" in themes:
            style.theme_use("vista")
        elif "clam" in themes:
            style.theme_use("clam")

        base = ("Segoe UI", 10)
        style.configure(".", font=base)
        style.configure("Title.TLabel", font=("Segoe UI Semibold", 22))
        style.configure("Subtitle.TLabel", font=("Segoe UI", 10), foreground="#5b6470")
        style.configure("Hint.TLabel", font=("Segoe UI", 9), foreground="#6b7280")
        style.configure("Status.TLabel", font=("Segoe UI", 9), foreground="#374151")
        style.configure("Accent.TButton", font=("Segoe UI Semibold", 10))
        style.configure("TLabelframe.Label", font=("Segoe UI Semibold", 10))

    def _build_ui(self) -> None:
        outer = ttk.Frame(self.root, padding=(22, 18, 22, 16))
        outer.pack(fill="both", expand=True)
        outer.columnconfigure(0, weight=1)
        outer.rowconfigure(3, weight=1)

        header = ttk.Frame(outer)
        header.grid(row=0, column=0, sticky="ew", pady=(0, 14))
        ttk.Label(header, text="Longman → Anki", style="Title.TLabel").pack(anchor="w")
        ttk.Label(
            header,
            text="Paste words, pick a part of speech, and export a ready-to-import text file.",
            style="Subtitle.TLabel",
        ).pack(anchor="w", pady=(2, 0))

        input_frame = ttk.LabelFrame(outer, text=" Words ", padding=(12, 8, 12, 10))
        input_frame.grid(row=1, column=0, sticky="ew", pady=(0, 12))
        input_frame.columnconfigure(0, weight=1)

        self.words_text = ScrolledText(
            input_frame,
            height=7,
            wrap="word",
            font=("Consolas", 11),
            undo=True,
            relief="solid",
            borderwidth=1,
        )
        self.words_text.grid(row=0, column=0, sticky="ew")
        self.words_text.bind("<KeyRelease>", lambda _event: self._update_word_count())
        self.words_text.bind("<<Paste>>", self._on_paste)

        hint_row = ttk.Frame(input_frame)
        hint_row.grid(row=1, column=0, sticky="ew", pady=(6, 0))
        hint_row.columnconfigure(0, weight=1)
        ttk.Label(
            hint_row,
            text="One word per line, or separate with commas / semicolons. "
                 "Phrases like “take off” are kept together.",
            style="Hint.TLabel",
        ).grid(row=0, column=0, sticky="w")
        self.count_label = ttk.Label(hint_row, text="0 words", style="Hint.TLabel")
        self.count_label.grid(row=0, column=1, sticky="e")

        options = ttk.LabelFrame(outer, text=" Options ", padding=(12, 10, 12, 12))
        options.grid(row=2, column=0, sticky="ew", pady=(0, 12))
        options.columnconfigure(1, weight=1)

        ttk.Label(options, text="Part of speech").grid(
            row=0, column=0, sticky="w", padx=(0, 10),
        )
        self.pos_var = tk.StringVar(value="Any")
        self.pos_box = ttk.Combobox(
            options,
            textvariable=self.pos_var,
            values=POS_OPTIONS,
            state="readonly",
            width=18,
        )
        self.pos_box.grid(row=0, column=1, sticky="w")

        ttk.Label(options, text="Output file").grid(
            row=1, column=0, sticky="w", padx=(0, 10), pady=(10, 0),
        )
        self.output_var = tk.StringVar(value=str(self._new_output_path()))
        output_row = ttk.Frame(options)
        output_row.grid(row=1, column=1, columnspan=2, sticky="ew", pady=(10, 0))
        output_row.columnconfigure(0, weight=1)
        self.output_entry = ttk.Entry(output_row, textvariable=self.output_var)
        self.output_entry.grid(row=0, column=0, sticky="ew")
        ttk.Button(output_row, text="Browse…", command=self._browse_output).grid(
            row=0, column=1, padx=(8, 0),
        )

        actions = ttk.Frame(options)
        actions.grid(row=2, column=1, columnspan=2, sticky="ew", pady=(12, 0))
        actions.columnconfigure(0, weight=1)
        self.generate_button = ttk.Button(
            actions,
            text="Generate cards",
            style="Accent.TButton",
            command=self.start_generation,
        )
        self.generate_button.grid(row=0, column=1)
        self.stop_button = ttk.Button(
            actions, text="Stop", command=self.stop_generation, state="disabled",
        )
        self.stop_button.grid(row=0, column=2, padx=(8, 0))
        self.open_button = ttk.Button(
            actions, text="Open file", command=self._open_last_output,
            state="disabled",
        )
        self.open_button.grid(row=0, column=3, padx=(8, 0))

        log_frame = ttk.LabelFrame(outer, text=" Progress ", padding=(12, 8, 12, 10))
        log_frame.grid(row=3, column=0, sticky="nsew")
        log_frame.columnconfigure(0, weight=1)
        log_frame.rowconfigure(2, weight=1)

        progress_row = ttk.Frame(log_frame)
        progress_row.grid(row=0, column=0, sticky="ew")
        progress_row.columnconfigure(0, weight=1)
        self.progress = ttk.Progressbar(progress_row, mode="determinate")
        self.progress.grid(row=0, column=0, sticky="ew")
        self.progress_label = ttk.Label(progress_row, text="Ready", style="Status.TLabel",
                                        width=24, anchor="e")
        self.progress_label.grid(row=0, column=1, padx=(10, 0))

        self.status_var = tk.StringVar(value="Paste your words above and click Generate cards.")
        ttk.Label(log_frame, textvariable=self.status_var, style="Status.TLabel").grid(
            row=1, column=0, sticky="w", pady=(6, 6),
        )

        self.log = ScrolledText(
            log_frame,
            height=12,
            wrap="word",
            font=("Consolas", 9),
            state="disabled",
            relief="solid",
            borderwidth=1,
        )
        self.log.grid(row=2, column=0, sticky="nsew")
        self.log.tag_configure("ok", foreground="#167a3d")
        self.log.tag_configure("error", foreground="#b42318")
        self.log.tag_configure("info", foreground="#5b6470")
        self.log.tag_configure("head", foreground="#1f2937", font=("Consolas", 9, "bold"))

        footer = ttk.Label(
            outer,
            text="Source: ldoceonline.com  •  Fields: Front, Word, Back, Audio  •  "
                 "Audio is exported empty",
            style="Hint.TLabel",
        )
        footer.grid(row=4, column=0, sticky="w", pady=(10, 0))

    # -- helpers --------------------------------------------------------

    @staticmethod
    def _new_output_path() -> Path:
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        return OUTPUT_DIR / f"longman_cards_{stamp}.txt"

    def _update_word_count(self) -> None:
        words = parse_word_list(self.words_text.get("1.0", "end"))
        self.count_label.configure(text=f"{len(words)} word{'s' if len(words) != 1 else ''}")

    def _on_paste(self, _event: tk.Event) -> None:
        self.root.after(30, self._update_word_count)

    def _browse_output(self) -> None:
        current = Path(self.output_var.get().strip() or self._new_output_path())
        chosen = filedialog.asksaveasfilename(
            parent=self.root,
            title="Save Anki import file",
            initialdir=str(current.parent),
            initialfile=current.name,
            defaultextension=".txt",
            filetypes=[("Text files", "*.txt"), ("All files", "*.*")],
        )
        if chosen:
            self.output_var.set(str(ensure_txt_suffix(chosen)))

    def _open_last_output(self) -> None:
        if self.last_output and self.last_output.exists():
            try:
                open_path(self.last_output)
            except OSError as exc:
                messagebox.showerror(APP_TITLE, f"Could not open the file:\n{exc}")

    def _append_log(self, text: str, tag: str = "info") -> None:
        self.log.configure(state="normal")
        self.log.insert("end", text + "\n", tag)
        self.log.see("end")
        self.log.configure(state="disabled")

    def _clear_log(self) -> None:
        self.log.configure(state="normal")
        self.log.delete("1.0", "end")
        self.log.configure(state="disabled")

    # -- generation -----------------------------------------------------

    def start_generation(self) -> None:
        if self.running:
            return

        words = parse_word_list(self.words_text.get("1.0", "end"))
        if not words:
            messagebox.showwarning(
                APP_TITLE,
                "Please enter at least one word.\n\n"
                "You can put one word per line or separate words with commas.",
            )
            self.words_text.focus_set()
            return

        raw_output = self.output_var.get().strip()
        if not raw_output:
            raw_output = str(self._new_output_path())
        output_path = ensure_txt_suffix(raw_output).expanduser().resolve()

        if output_path.exists():
            overwrite = messagebox.askyesno(
                APP_TITLE,
                f"This file already exists:\n\n{output_path}\n\nOverwrite it?",
            )
            if not overwrite:
                return
        self.output_var.set(str(output_path))

        part_of_speech = None if self.pos_var.get() == "Any" else self.pos_var.get()

        self.stop_event.clear()
        self.running = True
        self.last_output = None
        self.open_button.configure(state="disabled")
        self.generate_button.configure(state="disabled")
        self.stop_button.configure(state="normal")
        self.progress.configure(maximum=len(words), value=0)
        self.progress_label.configure(text=f"0 / {len(words)}")
        self.status_var.set(f"Generating {len(words)} card(s)…")
        self._clear_log()
        self._append_log(
            f"Words: {len(words)}   •   Part of speech: {part_of_speech or 'any'}",
            "head",
        )
        self._append_log(f"Output: {output_path}", "info")
        self._append_log("", "info")

        self.worker = threading.Thread(
            target=self._generate_worker,
            args=(words, part_of_speech, output_path),
            daemon=True,
        )
        self.worker.start()

    def stop_generation(self) -> None:
        if not self.running:
            return
        self.stop_event.set()
        self.status_var.set("Stopping after the current word…")
        self.stop_button.configure(state="disabled")

    def _generate_worker(self, words: list[str], part_of_speech: str | None,
                         output_path: Path) -> None:
        client = LongmanDictionary(stop_event=self.stop_event)
        cards: list[Card] = []
        failures = 0
        total = len(words)

        for index, word in enumerate(words, start=1):
            if self.stop_event.is_set():
                break

            self.queue.put(("status", f"Looking up “{word}”… ({index} / {total})"))
            try:
                card = client.lookup(word, part_of_speech)
            except LongmanError as exc:
                failures += 1
                self.queue.put(("error", word, str(exc)))
            except Exception as exc:  # unexpected parser/network failure
                failures += 1
                self.queue.put((
                    "error", word, f"Unexpected error: {type(exc).__name__}: {exc}",
                ))
            else:
                cards.append(card)
                self.queue.put(("card", card))
            finally:
                self.queue.put(("progress", index, total))

        written: Path | None = None
        if cards:
            try:
                written = write_anki_file(output_path, cards)
            except OSError as exc:
                self.queue.put((
                    "done", len(cards), failures + 1, None, True,
                    f"Could not write the output file: {exc}",
                ))
                return

        self.queue.put((
            "done", len(cards), failures, written, self.stop_event.is_set(), "",
        ))

    # -- queue polling --------------------------------------------------

    def _poll_queue(self) -> None:
        try:
            while True:
                message = self.queue.get_nowait()
                kind = message[0]
                if kind == "status":
                    self.status_var.set(message[1])
                elif kind == "progress":
                    current, total = message[1], message[2]
                    self.progress.configure(value=current)
                    self.progress_label.configure(text=f"{current} / {total}")
                elif kind == "card":
                    card: Card = message[1]
                    suffix = ""
                    if card.american_of:
                        suffix = f"  [British: {card.american_of}]"
                    self._append_log(
                        f"✓ {card.word} [{card.part_of_speech}]{suffix}  →  {card.front}",
                        "ok",
                    )
                    if card.example_url != card.url:
                        self._append_log(f"   examples from: {card.example_url}", "info")
                elif kind == "error":
                    self._append_log(f"✗ {message[1]}  →  {message[2]}", "error")
                elif kind == "done":
                    self._finish(*message[1:])
        except queue.Empty:
            pass
        self.root.after(90, self._poll_queue)

    def _finish(self, cards_count: int, failures: int, path: Path | None,
                stopped: bool, write_error: str) -> None:
        self.running = False
        self.generate_button.configure(state="normal")
        self.stop_button.configure(state="disabled")

        if write_error:
            self.status_var.set("Finished with an error.")
            self._append_log("", "info")
            self._append_log(write_error, "error")
            messagebox.showerror(APP_TITLE, write_error)
            return

        if path is not None:
            self.last_output = path
            self.open_button.configure(state="normal")
            self._append_log("", "info")
            self._append_log(f"Saved {cards_count} card(s) to:", "head")
            self._append_log(str(path), "info")
        else:
            self._append_log("", "info")
            self._append_log("No cards were generated, so no file was written.", "error")

        summary = f"{cards_count} card(s) generated"
        if failures:
            summary += f", {failures} skipped"
        if stopped:
            summary = "Stopped. " + summary
        self.status_var.set(summary + ".")
        self.progress_label.configure(text="Done")

    def _on_close(self) -> None:
        if self.running:
            close = messagebox.askyesno(
                APP_TITLE,
                "A generation is still running. Stop it and close the app?",
            )
            if not close:
                return
            self.stop_event.set()
        self.root.destroy()


def main() -> None:
    root = tk.Tk()
    CardGeneratorApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()
