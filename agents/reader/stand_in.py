"""A stand-in for the noter and writer models, with no model at all.

    python agents/reader/read.py BOOK.pdf --out trial --model-command python agents/reader/stand_in.py

It checks the plumbing, not the notes: the noter's ideas are sentences
copied from the piece, and the writer's cards group them three at a time.
Use it to see that a book is split, read, checked, drawn and scored end to
end before spending a real model's time on it. Its notes are not study notes.
"""

from __future__ import annotations

import json
import re
import sys

raw = sys.stdin.read()
# Kosika sends "[system]\n...\n\n[user]\n" and the job as JSON: {"text": "..."}
user = raw.split("[user]\n", 1)[-1].strip()
try:
    prompt = json.loads(user)["text"]
except (ValueError, KeyError, TypeError):
    prompt = user

if "The text:\n" in prompt:
    text = prompt.split("The text:\n", 1)[1]
    sentences = [s.strip() for s in re.split(r"(?<=[.!?])\s+", text.replace("\n", " ")) if len(s.split()) >= 8]
    sentences.sort(key=lambda s: -sum(len(w) > 6 for w in s.split()))
    ideas = []
    for s in sentences[:5]:
        quote = " ".join(s.split()[:12])
        kind = "how" if " by " in s or "step" in s else "why" if "because" in s or " so " in s else "what"
        ideas.append({"kind": kind, "idea": s, "quote": quote})
    print(json.dumps({"ideas": ideas}))
else:
    listed = re.findall(r"^(\d+)\. \[[a-z]+, p\. \d+\] (.+)$", prompt, re.M)
    cards = []
    for i in range(0, len(listed), 3):
        group = listed[i:i + 3]
        first = group[0][1]
        cards.append({"concept": " ".join(first.split()[:4]).strip(".,:;"), "what": first, "how": "", "why": "",
                      "example": "", "bullets": [g[1] for g in group[1:]], "from": [int(g[0]) for g in group]})
    title = re.search(r'chapter "([^"]+)"', prompt)
    print(json.dumps({"gist": f"Stand-in gist for {title.group(1) if title else 'this chapter'}: the plumbing works.", "cards": cards}))
