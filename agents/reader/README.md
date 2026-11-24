# Reader: a whole book into study notes

Give it a PDF, an EPUB, a web book or a text file of 300 to 400 pages. It
reads every page, A to Z, and writes notes you can learn the book from in a
fraction of the time: for each chapter a short overview, then one card per
concept with **what** it is, **how** it is done, **why** it is done, an
example and the key points, each with the page and the exact words of the
book it came from. Each chapter also gets a mind map drawn by Kosika Designs,
and the book's own figures and tables are listed by page.

Everything it learns is kept in its memory, so other Kosika agents can
recall later what was read.

## One line

```
python agents/reader/read.py BOOK.pdf --out notes/BOOK --model kosika-virtual:soft --model-url http://127.0.0.1:8791/v1
```

`--pages 1-60` reads only those pages first, as a quick trial. Any free
model works: the router, Groq or OpenRouter `:free`, or Ollama on the laptop
(`--model qwen2.5:3b --model-protocol ollama`). The notes land in `--out` as
`notes.html` (to read), `notes.md`, and `score.json`.

To check the plumbing with no model at all:

```
python agents/reader/read.py BOOK.pdf --out trial --model-command python agents/reader/stand_in.py
```

## How it stays honest

Code does every exact job; the models only write.

| Step | Who | What is checked |
| --- | --- | --- |
| Text out of the book, page by page | `pages.py` | nothing is summarised or dropped |
| Chapters (the book's own contents, else its headings, else 15 pages each) and pieces of about 5000 characters | Kosika code | every line is in exactly one piece |
| Ideas from each piece | noter model | each idea's quote is found in the piece word for word; one not found is dropped and counted, and the piece is asked again |
| Concept cards from each chapter's ideas | writer model | each card names the ideas it uses, four in five ideas must be used, and a card with many words the chapter does not have is marked as drifting |
| Coverage | Kosika code | every chapter has notes, every piece gave ideas (or was contents, an index or references), the chapter's key words are in its notes |

## The scorecard

Every run ends with a scorecard in the notes and in `score.json`: pages
read, chapters with notes, pieces that gave checked ideas, ideas kept and
dropped, cards drifting from the book, key words covered, and how many
times shorter the notes are than the book. It passes only when every
chapter has notes, nine pieces in ten gave checked ideas, at most one card
in ten drifts, and seven key words in ten are covered.

## Memory

Each checked idea is kept in the memory `shelf` (in `--out/memory`, or a
folder shared with other agents through `--memory`):
`{book, chapter, page, kind, idea, quote}`. An agent recalls it with
`recall shelf like "how does a hash table grow"`.

## What it cannot do yet

- A scanned PDF has no text to take out; it needs OCR first.
- Figures and diagrams in the book are listed with their page and caption,
  not copied; the mind maps are drawn new from the notes.
- The Go engine stops a run at 64 model calls and has no flag to raise it,
  so a whole book runs on the Python engine (`--engine kosika`, the
  default) until kosika-native takes `--max-model-calls`.
- Time: one model call per 5000 characters, so a 400-page book is about
  200 to 250 calls. On a free cloud model that is under an hour; on Ollama
  on the laptop's processor it is a night.
