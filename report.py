"""Read-only HTML export with native browser Persian shaping and bidirectional text."""

from __future__ import annotations

import html
import json
import re
from pathlib import Path

from provider import TutorError


def readable_text(text: str) -> str:
    # Isolate ASCII runs (especially formulas) inside Persian paragraphs. Escape
    # every segment so neither user text nor model output can introduce markup.
    pattern = re.compile(r"[A-Za-z0-9][A-Za-z0-9 \t_*+^=/.,()'\-\[\]{}:]*")
    parts = []
    cursor = 0
    for match in pattern.finditer(text):
        parts.append(html.escape(text[cursor:match.start()]))
        run = match.group().rstrip(" \t")
        spacing = match.group()[len(run):]
        parts.append('<bdi dir="ltr">' + html.escape(run) + '</bdi>' + html.escape(spacing))
        cursor = match.end()
    parts.append(html.escape(text[cursor:]))
    return "".join(parts)


def write_html_report(state: dict, path: Path) -> None:
    if path.suffix.lower() not in {".html", ".htm"}:
        raise TutorError("Reports must have a .html or .htm extension.")
    fa = state["language"] == "fa"
    direction = "rtl" if fa else "ltr"
    language = "fa" if fa else "en"
    labels = (["گزارش مدرس ریاضی مایلو", "سؤال", "پاسخ", "مسیر اجرا", "مشاهدهٔ State"]
              if fa else ["MILO Mathematics Tutor Report", "Question", "Answer", "Execution route", "Inspect State"])
    route = " → ".join(event["node"] for event in state["trace"])
    metadata = " | ".join(str(state[key]) for key in ("status", "learner_level", "verification"))
    document = f'''<!doctype html>
<html lang="{language}" dir="{direction}">
<head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>{labels[0]}</title>
<style>
body{{margin:0;background:#f2f5fa;color:#20304a;font:18px/1.9 Tahoma,"Segoe UI",sans-serif;}}
main{{max-width:1000px;margin:36px auto;padding:32px;background:white;border-radius:18px;box-shadow:0 8px 35px #172b4512;}}
h1{{font-size:27px;margin-top:0;}}h2{{font-size:20px;color:#6650ad;margin-bottom:8px;}}
.text{{white-space:pre-wrap;overflow-wrap:anywhere;unicode-bidi:plaintext;}}
.meta{{font-size:14px;color:#607089;}}pre{{white-space:pre-wrap;overflow-wrap:anywhere;background:#f4f1fb;padding:16px;border-radius:10px;font:14px/1.7 Consolas,monospace;text-align:left;}}
details{{margin-top:24px;}}summary{{cursor:pointer;color:#6650ad;}}
@media(max-width:700px){{main{{margin:0;padding:20px;border-radius:0;}}}}
</style></head>
<body><main><h1>{labels[0]}</h1>
<p class="meta" dir="ltr">{html.escape(metadata)}</p>
<h2>{labels[1]}</h2><div class="text" dir="auto">{readable_text(state["raw_request"])}</div>
<h2>{labels[2]}</h2><div class="text" dir="auto">{readable_text(state["final_answer"])}</div>
<h2>{labels[3]}</h2><pre dir="ltr">{html.escape(route)}</pre>
<details><summary>{labels[4]}</summary><pre dir="ltr">{html.escape(json.dumps(state,ensure_ascii=False,indent=2))}</pre></details>
</main></body></html>'''
    try:
        path.write_text(document, encoding="utf-8")
    except OSError:
        raise TutorError("Cannot write the report. Check that the output folder exists and is writable.") from None
