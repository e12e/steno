"""Sanitized Markdown and interactive HTML transcript/notes renderers."""

from __future__ import annotations

import html
import re
from html.parser import HTMLParser
from pathlib import Path

from mote.constants import TurnDict, format_seconds
from mote.template_loader import load_template

_SAFE_HREF_RE = re.compile(r"^(https?://|mailto:|#)", re.IGNORECASE)


def _is_safe_href(url: str) -> bool:
    """Allow only safe web, mailto, or fragment link schemes in generated HTML."""
    clean = html.unescape(url or "").strip()
    if any(ord(ch) < 32 for ch in clean):
        return False
    return bool(_SAFE_HREF_RE.match(clean))


_ALLOWED_HTML_TAGS = frozenset({
    "h1", "h2", "h3", "h4", "h5", "h6",
    "p", "br", "hr",
    "strong", "b", "em", "i", "code", "pre", "blockquote",
    "ul", "ol", "li",
    "table", "thead", "tbody", "tr", "th", "td",
    "a", "del", "sup", "sub",
})
_VOID_HTML_TAGS = frozenset({"br", "hr"})


class _MarkdownHtmlSanitizer(HTMLParser):
    """Strip unsafe tags, event handlers, and javascript:/data: links from rendered Markdown HTML."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=False)
        self.out: list[str] = []
        self._skip_depth = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        low = tag.lower()
        if low in ("script", "style"):
            self._skip_depth += 1
            return
        if self._skip_depth > 0:
            return
        if low not in _ALLOWED_HTML_TAGS:
            return
        attr_parts: list[str] = []
        if low == "a":
            href = ""
            for k, v in attrs:
                if k.lower() == "href" and v and _is_safe_href(v):
                    href = v.strip()
                    break
            if href:
                attr_parts.append(f'href="{html.escape(href, quote=True)}"')
                attr_parts.append('target="_blank"')
                attr_parts.append('rel="noopener noreferrer"')
        elif low in ("th", "td"):
            for k, v in attrs:
                if k.lower() == "align" and v in ("left", "center", "right"):
                    attr_parts.append(f'align="{v}"')
        attr_str = (" " + " ".join(attr_parts)) if attr_parts else ""
        self.out.append(f"<{low}{attr_str}>")

    def handle_endtag(self, tag: str) -> None:
        low = tag.lower()
        if low in ("script", "style"):
            if self._skip_depth > 0:
                self._skip_depth -= 1
            return
        if self._skip_depth > 0 or low not in _ALLOWED_HTML_TAGS or low in _VOID_HTML_TAGS:
            return
        self.out.append(f"</{low}>")

    def handle_data(self, data: str) -> None:
        if self._skip_depth == 0:
            self.out.append(html.escape(data, quote=False))

    def handle_entityref(self, name: str) -> None:
        if self._skip_depth == 0:
            self.out.append(f"&{name};")

    def handle_charref(self, name: str) -> None:
        if self._skip_depth == 0:
            self.out.append(f"&#{name};")


def _sanitize_rendered_html(raw_html: str) -> str:
    parser = _MarkdownHtmlSanitizer()
    parser.feed(raw_html)
    parser.close()
    return "".join(parser.out)


def _format_inline_md(text: str) -> str:
    """Format inline markdown elements: bold, italics, code, links, safely escaped."""
    escaped = html.escape(text)
    escaped = re.sub(r"`([^`]+)`", r"<code>\1</code>", escaped)
    escaped = re.sub(r"\*\*([^*]+)\*\*", r"<strong>\1</strong>", escaped)
    escaped = re.sub(r"(?<!\*)\*([^*]+)\*(?!\*)", r"<em>\1</em>", escaped)

    def _replace_link(match: re.Match[str]) -> str:
        label, url = match.group(1), match.group(2)
        if _is_safe_href(url):
            return f'<a href="{url.strip()}" target="_blank" rel="noopener noreferrer">{label}</a>'
        return label

    escaped = re.sub(r"\[([^\]]+)\]\(([^()\s]*(?:\([^()]*\)[^()\s]*)*)\)", _replace_link, escaped)
    return escaped


def _render_markdown_to_html(md_text: str) -> str:
    """Render markdown into clean semantic HTML using markdown library if available, with pure-Python fallback."""
    try:
        import markdown as md_lib

        raw_html = md_lib.markdown(
            md_text,
            extensions=["tables", "fenced_code", "sane_lists", "nl2br"],
        )
        return _sanitize_rendered_html(raw_html)
    except Exception:
        pass

    lines = md_text.splitlines()
    out: list[str] = []
    in_table = False
    in_code = False
    code_block: list[str] = []
    in_ul = False
    in_ol = False

    def close_blocks() -> None:
        nonlocal in_table, in_ul, in_ol
        if in_table:
            out.append("</tbody></table>")
            in_table = False
        if in_ul:
            out.append("</ul>")
            in_ul = False
        if in_ol:
            out.append("</ol>")
            in_ol = False

    for line in lines:
        stripped = line.strip()
        if stripped.startswith("```"):
            close_blocks()
            if in_code:
                out.append(f"<pre><code>{html.escape(chr(10).join(code_block))}</code></pre>")
                code_block.clear()
                in_code = False
            else:
                in_code = True
            continue
        if in_code:
            code_block.append(line)
            continue

        if not stripped:
            close_blocks()
            continue

        if stripped.startswith("#### "):
            close_blocks()
            out.append(f"<h4>{_format_inline_md(stripped[5:])}</h4>")
            continue
        if stripped.startswith("### "):
            close_blocks()
            out.append(f"<h3>{_format_inline_md(stripped[4:])}</h3>")
            continue
        if stripped.startswith("## "):
            close_blocks()
            out.append(f"<h2>{_format_inline_md(stripped[3:])}</h2>")
            continue
        if stripped.startswith("# "):
            close_blocks()
            out.append(f"<h1>{_format_inline_md(stripped[2:])}</h1>")
            continue

        if stripped in ("---", "***", "___"):
            close_blocks()
            out.append("<hr>")
            continue

        if stripped.startswith("|") and stripped.endswith("|"):
            raw_cells = [c.strip() for c in stripped.strip("|").split("|")]
            if all(re.match(r"^:?-+:?$", c) for c in raw_cells if c):
                continue
            if not in_table:
                close_blocks()
                out.append(
                    "<table><thead><tr>"
                    + "".join(f"<th>{_format_inline_md(c)}</th>" for c in raw_cells)
                    + "</tr></thead><tbody>"
                )
                in_table = True
            else:
                out.append("<tr>" + "".join(f"<td>{_format_inline_md(c)}</td>" for c in raw_cells) + "</tr>")
            continue

        if stripped.startswith("> "):
            close_blocks()
            out.append(f"<blockquote><p>{_format_inline_md(stripped[2:])}</p></blockquote>")
            continue

        if stripped.startswith(("- ", "* ", "+ ")):
            if in_table or in_ol:
                close_blocks()
            if not in_ul:
                out.append("<ul>")
                in_ul = True
            out.append(f"<li>{_format_inline_md(stripped[2:])}</li>")
            continue

        m_num = re.match(r"^\d+\.\s+(.+)$", stripped)
        if m_num:
            if in_table or in_ul:
                close_blocks()
            if not in_ol:
                out.append("<ol>")
                in_ol = True
            out.append(f"<li>{_format_inline_md(m_num.group(1))}</li>")
            continue

        close_blocks()
        out.append(f"<p>{_format_inline_md(stripped)}</p>")

    close_blocks()
    if in_code:
        out.append(f"<pre><code>{html.escape(chr(10).join(code_block))}</code></pre>")
    return "\n".join(out)


_FALLBACK_TRANSCRIPT_TPL = """<!DOCTYPE html>
<html lang="no">
<head><meta charset="utf-8"><title>{escaped_title} – Transkripsjon</title></head>
<body>
  <h1>{escaped_title} ({total_turns} innlegg, {speaker_count} deltakere, {total_duration_str})</h1>
  <div>{speaker_filters}</div>
  <div>{speaker_inputs}</div>
  <main id="container">{html_items}</main>
  <script>function saveUpdatedHtml() {{}}</script>
</body>
</html>"""

_FALLBACK_NOTES_TPL = """<!DOCTYPE html>
<html lang="no">
<head><meta charset="utf-8"><title>{escaped_title} – Møtereferat</title></head>
<body>
  <h1>{escaped_title}</h1>
  <article>{rendered_body}</article>
  <textarea id="raw-markdown" style="display:none">{escaped_notes_md}</textarea>
</body>
</html>"""


def build_transcript_html_and_text(
    turns: list[TurnDict],
    speaker_map: dict[str, str],
    html_path: Path,
    title: str,
) -> str:
    """Build rich searchable HTML transcript and return plain-text transcript for LLM."""
    colors = [
        "#2563EB", "#16A34A", "#D97706", "#9333EA", "#E11D48",
        "#0D9488", "#4F46E5", "#CA8A04", "#0284C7", "#BE185D",
    ]
    speaker_color_map: dict[str, str] = {}
    llm_lines: list[str] = []
    html_items: list[str] = []

    total_turns = len(turns)
    total_seconds = (turns[-1]["end"] - turns[0]["start"]) if turns else 0.0
    total_duration_str = format_seconds(total_seconds) if turns else "00:00"

    for idx, item in enumerate(turns, start=1):
        orig_spk = item["speaker"]
        display_spk = speaker_map.get(orig_spk, orig_spk)
        if orig_spk not in speaker_color_map:
            speaker_color_map[orig_spk] = colors[len(speaker_color_map) % len(colors)]
        color = speaker_color_map[orig_spk]
        t_str = f"{format_seconds(item['start'])} – {format_seconds(item['end'])}"

        llm_lines.append(f"[{t_str}] {display_spk}: {item['text']}")
        html_items.append(
            f'<div class="turn" data-speaker="{html.escape(orig_spk)}" id="turn-{idx}">'
            f'  <div class="meta">'
            f'    <span class="badge" style="background:{color}" data-spk-label="{html.escape(orig_spk)}">{html.escape(display_spk)}</span>'
            f'    <span class="timestamp">{t_str}</span>'
            f'    <button class="copy-turn-btn no-print" onclick="copyTurnText(\'turn-{idx}\')" title="Kopier innlegg">Kopier</button>'
            f'  </div>'
            f'  <div class="text">{html.escape(item["text"])}</div>'
            f'</div>'
        )

    speaker_filters = "".join(
        f'<button class="spk-filter-btn" data-spk="{html.escape(spk)}" onclick="filterBySpeaker(this.dataset.spk)">'
        f'  <span class="dot" style="background:{col}"></span>'
        f'  <span data-spk-btn-label="{html.escape(spk)}">{html.escape(speaker_map.get(spk, spk))}</span>'
        f'</button>'
        for spk, col in speaker_color_map.items()
    )

    speaker_inputs = "".join(
        f'<div class="spk-edit">'
        f'  <span class="dot" style="background:{col}"></span>'
        f'  <input type="text" value="{html.escape(speaker_map.get(spk, spk))}" data-orig="{html.escape(spk)}" '
        f'         oninput="renameSpeaker(this.dataset.orig, this.value)" title="Endre navn i visningen">'
        f'</div>'
        for spk, col in speaker_color_map.items()
    )

    speaker_count = len(speaker_color_map)

    tpl = load_template("transcript.html", _FALLBACK_TRANSCRIPT_TPL)
    html_doc = tpl.format(
        escaped_title=html.escape(title),
        total_turns=total_turns,
        speaker_count=speaker_count,
        total_duration_str=total_duration_str,
        speaker_filters=speaker_filters,
        speaker_inputs=speaker_inputs,
        html_items="".join(html_items),
    )
    html_path.write_text(html_doc, encoding="utf-8")
    return "\n".join(llm_lines)


def build_notes_html(notes_md: str, notes_html_path: Path, title: str) -> None:
    """Render clean Markdown meeting notes into a printable, beautifully styled HTML document."""
    rendered_body = _render_markdown_to_html(notes_md)

    tpl = load_template("notes.html", _FALLBACK_NOTES_TPL)
    html_doc = tpl.format(
        escaped_title=html.escape(title),
        rendered_body=rendered_body,
        escaped_notes_md=html.escape(notes_md),
    )
    notes_html_path.write_text(html_doc, encoding="utf-8")
