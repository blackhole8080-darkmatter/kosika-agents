"""Turn a book into pages Kosika can read: book.json for agents/reader.

    python agents/reader/pages.py BOOK.pdf --out book.json
    python agents/reader/pages.py https://example.org/book.html --out book.json

Kosika cannot open a PDF or an EPUB itself yet, so this one step is done
here, and only this: it takes the text out, page by page, and the book's
own table of contents when it has one. Nothing is summarised, guessed or
dropped. Splitting into chapters, chunking, reading, checking and scoring
are all done by the Kosika program, main.kos.

Reads .pdf (pypdf, else poppler's pdftotext), .epub, .html/.htm, .md and
.txt, from a file or an http(s) address. A web page or a text file has no
pages, so it is cut into pages of about 3000 characters at paragraph ends, and a new page at each
"# " or "## " heading, so no two chapters share a page.

The result is the input main.kos takes:

    {"book": {"title": ..., "source": ..., "pages": ["page 1 text", ...],
              "outline": [{"title": "1 Introduction", "page": 1}, ...]}}

Pages are counted from 1. outline is empty when the book has none; then
main.kos finds the chapters from the headings on the pages.
"""

from __future__ import annotations

import argparse
import html
import json
import re
import shutil
import subprocess
import sys
import tempfile
import urllib.request
import zipfile
from html.parser import HTMLParser
from pathlib import Path

PAGE_SIZE = 3000


def tidy(text: str) -> str:
    """Join words hyphenated across a line end and drop stray spaces."""
    text = text.replace("­", "").replace("\r\n", "\n").replace("\r", "\n")
    text = re.sub(r"(\w)-\n(\w)", r"\1\2", text)
    text = re.sub(r"[ \t\f\v]+", " ", text)
    text = re.sub(r" *\n *", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def cut_pages(text: str, size: int = PAGE_SIZE) -> list[str]:
    """Cut a text with no pages of its own into pages at paragraph ends,
    starting a new page at every top heading."""
    pages: list[str] = []
    page = ""
    for para in re.split(r"\n\s*\n", text):
        para = para.strip()
        if not para:
            continue
        # a chapter heading starts a page of its own, so chapters never share one
        heading = para.startswith("# ") or para.startswith("## ")
        if page and (heading or len(page) + len(para) > size):
            pages.append(page)
            page = ""
        page = f"{page}\n\n{para}" if page else para
    if page:
        pages.append(page)
    return pages


# ------------------------------------------------------------------ PDF


def pdf_outline(reader) -> list[dict]:
    """The PDF's own bookmarks, top level only: one per chapter."""
    marks: list[dict] = []
    try:
        outline = reader.outline
    except Exception:  # a broken outline is no outline
        return marks
    for item in outline:
        if isinstance(item, list):  # sub-sections of the item before
            continue
        try:
            page = reader.get_destination_page_number(item) + 1
        except Exception:
            continue
        title = " ".join(str(item.title).split())
        if title:
            marks.append({"title": title, "page": page})
    marks.sort(key=lambda m: m["page"])
    return marks


def read_pdf(path: Path) -> tuple[str, list[str], list[dict]]:
    try:
        from pypdf import PdfReader
    except ImportError:
        PdfReader = None
    if PdfReader is not None:
        reader = PdfReader(str(path))
        pages = [tidy(p.extract_text() or "") for p in reader.pages]
        title = ""
        if reader.metadata and reader.metadata.title:
            title = str(reader.metadata.title).strip()
        return title, pages, pdf_outline(reader)
    if shutil.which("pdftotext") is None:
        sys.exit("Reading a PDF needs pypdf (pip install pypdf) or poppler's pdftotext.")
    done = subprocess.run(["pdftotext", "-layout", str(path), "-"], capture_output=True, text=True, check=True)
    pages = [tidy(p) for p in done.stdout.split("\f")]
    if pages and not pages[-1]:
        pages.pop()
    return "", pages, []


# ------------------------------------------------------------------ HTML / EPUB


class _Text(HTMLParser):
    """The readable text of a page, with headings kept as Markdown headings
    so main.kos can find the chapters."""

    SKIP = {"script", "style", "nav", "header", "footer", "aside", "noscript"}
    BLOCK = {"p", "div", "li", "br", "tr", "pre", "blockquote", "section", "article", "figcaption", "dd", "dt"}

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.out: list[str] = []
        self.skipping = 0
        self.heading = 0
        self.title = ""
        self.in_title = False

    def handle_starttag(self, tag, attrs):
        if tag in self.SKIP:
            self.skipping += 1
        elif tag == "title":
            self.in_title = True
        elif re.fullmatch(r"h[1-6]", tag):
            self.heading = int(tag[1])
            self.out.append("\n\n" + "#" * min(self.heading, 3) + " ")
        elif tag in self.BLOCK:
            self.out.append("\n")

    def handle_endtag(self, tag):
        if tag in self.SKIP and self.skipping:
            self.skipping -= 1
        elif tag == "title":
            self.in_title = False
        elif re.fullmatch(r"h[1-6]", tag):
            self.heading = 0
            self.out.append("\n\n")
        elif tag in self.BLOCK:
            self.out.append("\n")

    def handle_data(self, data):
        if self.in_title:
            self.title += data
        elif not self.skipping:
            self.out.append(data.replace("\n", " ") if self.heading else data)

    def text(self) -> str:
        return tidy("".join(self.out))


def html_text(markup: str) -> tuple[str, str]:
    parser = _Text()
    parser.feed(markup)
    return " ".join(parser.title.split()), parser.text()


def read_epub(path: Path) -> tuple[str, str]:
    """An EPUB's chapters in reading order, as one text."""
    with zipfile.ZipFile(path) as book:
        container = book.read("META-INF/container.xml").decode("utf-8", "replace")
        opf_path = re.search(r'full-path="([^"]+)"', container).group(1)
        opf = book.read(opf_path).decode("utf-8", "replace")
        base = opf_path.rsplit("/", 1)[0] + "/" if "/" in opf_path else ""
        items = dict(re.findall(r'<item\b[^>]*\bid="([^"]+)"[^>]*\bhref="([^"]+)"', opf))
        items.update({i: h for h, i in re.findall(r'<item\b[^>]*\bhref="([^"]+)"[^>]*\bid="([^"]+)"', opf)})
        order = re.findall(r'<itemref\b[^>]*\bidref="([^"]+)"', opf)
        found = re.search(r"<dc:title[^>]*>(.*?)</dc:title>", opf, re.S)
        title = html.unescape(found.group(1)).strip() if found else ""
        parts = []
        for ref in order:
            href = items.get(ref)
            if not href:
                continue
            try:
                parts.append(html_text(book.read(base + href).decode("utf-8", "replace"))[1])
            except KeyError:
                continue
    return title, "\n\n".join(parts)


# ------------------------------------------------------------------ any source


def fetch(address: str, folder: Path) -> Path:
    name = re.sub(r"[^A-Za-z0-9._-]", "_", address.rstrip("/").rsplit("/", 1)[-1]) or "page.html"
    target = folder / name
    request = urllib.request.Request(address, headers={"User-Agent": "kosika-reader"})
    with urllib.request.urlopen(request, timeout=60) as reply:
        kind = reply.headers.get("Content-Type", "")
        target.write_bytes(reply.read())
    if "html" in kind and target.suffix.lower() not in {".html", ".htm"}:
        target = target.rename(target.with_suffix(".html"))
    if "pdf" in kind and target.suffix.lower() != ".pdf":
        target = target.rename(target.with_suffix(".pdf"))
    return target


def read_book(source: str) -> dict:
    with tempfile.TemporaryDirectory(prefix="kosika-reader-") as folder:
        path = fetch(source, Path(folder)) if re.match(r"https?://", source) else Path(source)
        kind = path.suffix.lower()
        outline: list[dict] = []
        if kind == ".pdf":
            title, pages, outline = read_pdf(path)
        elif kind == ".epub":
            title, text = read_epub(path)
            pages = cut_pages(text)
        elif kind in {".html", ".htm", ".xhtml"}:
            title, text = html_text(path.read_text("utf-8", "replace"))
            pages = cut_pages(text)
        else:
            text = tidy(path.read_text("utf-8", "replace"))
            title = ""
            pages = cut_pages(text)
    title = title or Path(source.rstrip("/")).stem.replace("_", " ").replace("-", " ")
    return {"book": {"title": title, "source": source, "pages": pages, "outline": outline}}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("source", help="a .pdf, .epub, .html, .md or .txt file, or an http(s) address")
    parser.add_argument("--out", default="book.json")
    parser.add_argument("--first", type=int, default=1, help="first page to keep, from 1")
    parser.add_argument("--last", type=int, default=0, help="last page to keep (0: to the end)")
    args = parser.parse_args(argv)
    book = read_book(args.source)
    pages = book["book"]["pages"]
    last = args.last or len(pages)
    if args.first > 1 or last < len(pages):
        book["book"]["pages"] = pages[args.first - 1:last]
        book["book"]["outline"] = [
            {"title": m["title"], "page": m["page"] - args.first + 1}
            for m in book["book"]["outline"] if args.first <= m["page"] <= last
        ]
    Path(args.out).write_text(json.dumps(book, ensure_ascii=False), "utf-8")
    kept = book["book"]
    words = sum(len(p.split()) for p in kept["pages"])
    print(f"{kept['title']}: {len(kept['pages'])} pages, {words} words, "
          f"{len(kept['outline'])} chapters in its own contents -> {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
