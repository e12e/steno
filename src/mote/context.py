"""Transcript parsing, document context extraction (.pptx/.docx/.pdf), and SSRF-guarded URL fetching."""

from __future__ import annotations

import html
import ipaddress
import json
import os
import re
import shutil
import socket
import subprocess
import sys
import tempfile
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
import zipfile
import zlib
from collections.abc import Callable
from pathlib import Path
from typing import Any

from mote.constants import CONTEXT_DOC_EXTENSIONS, TurnDict, parse_time_str, sanitize_text


def extract_turns_from_whisperx_data(data: dict[str, Any]) -> list[TurnDict]:
    """Extract word-level speaker turns from parsed WhisperX JSON dict and merge adjacent same-speaker words.

    >>> sample = {
    ...     "segments": [
    ...         {
    ...             "start": 0.0,
    ...             "end": 3.0,
    ...             "speaker": "SPEAKER_00",
    ...             "words": [
    ...                 {"word": "Hei", "start": 0.0, "end": 0.5, "speaker": "SPEAKER_00"},
    ...                 {"word": "der.", "start": 0.6, "end": 1.0, "speaker": "SPEAKER_00"},
    ...                 {"word": "Hallo!", "start": 1.2, "end": 2.0, "speaker": "SPEAKER_01"},
    ...             ],
    ...         }
    ...     ]
    ... }
    >>> turns = extract_turns_from_whisperx_data(sample)
    >>> [(t["speaker"], t["text"]) for t in turns]
    [('SPEAKER_00', 'Hei der.'), ('SPEAKER_01', 'Hallo!')]
    """
    raw_segments = data.get("segments", [])
    atomic_items: list[TurnDict] = []
    last_speaker = "SPEAKER_00"

    for seg in raw_segments:
        seg_speaker = str(seg.get("speaker") or last_speaker)
        words = seg.get("words") or []

        if words and any(isinstance(w, dict) and w.get("speaker") for w in words):
            for w in words:
                if not isinstance(w, dict):
                    continue
                w_text = sanitize_text(str(w.get("word", "")))
                if not w_text:
                    continue
                w_spk = str(w.get("speaker") or seg_speaker or last_speaker)
                last_speaker = w_spk
                w_start = float(w.get("start", seg.get("start", 0.0)))
                w_end = float(w.get("end", w_start))
                atomic_items.append(
                    {"speaker": w_spk, "start": w_start, "end": w_end, "text": w_text}
                )
        else:
            text = sanitize_text(str(seg.get("text", "")))
            if not text:
                continue
            start = float(seg.get("start", 0.0))
            end = float(seg.get("end", start))
            last_speaker = seg_speaker
            atomic_items.append(
                {"speaker": seg_speaker, "start": start, "end": end, "text": text}
            )

    merged: list[TurnDict] = []
    for item in atomic_items:
        spk = item["speaker"]
        start = item["start"]
        end = item["end"]
        text = item["text"]
        if merged and merged[-1]["speaker"] == spk and (start - merged[-1]["end"]) < 2.0:
            merged[-1]["end"] = max(merged[-1]["end"], end)
            merged[-1]["text"] += " " + text
        else:
            merged.append({"speaker": spk, "start": start, "end": end, "text": text})

    return merged


def extract_turns_from_whisperx(json_path: Path) -> list[TurnDict]:
    """Load WhisperX JSON file and extract speaker turns."""
    raw_bytes = json_path.read_bytes()
    data = json.loads(raw_bytes.decode("utf-8", errors="replace"))
    return extract_turns_from_whisperx_data(data)


def parse_transcript_text(raw_text: str, ext: str) -> tuple[list[TurnDict], dict[str, str]]:
    """Parse turns and speaker_map from raw transcript content (.html, .json, .srt, .vtt, .txt, .md).

    >>> turns, smap = parse_transcript_text("[00:01 – 00:05] Kari: Velkommen til møtet.\\n[00:06 – 00:10] Ola: Takk!", ".txt")
    >>> [(t["speaker"], t["start"], t["text"]) for t in turns]
    [('Kari', 1.0, 'Velkommen til møtet.'), ('Ola', 6.0, 'Takk!')]
    >>> srt = "1\\n00:00:01,000 --> 00:00:04,500\\nIngrid: Sikkerhetsrapporten er klar.\\n"
    >>> turns_srt, _ = parse_transcript_text(srt, ".srt")
    >>> (turns_srt[0]["speaker"], turns_srt[0]["end"], turns_srt[0]["text"])
    ('Ingrid', 4.5, 'Sikkerhetsrapporten er klar.')
    """
    ext = ext.lower()
    turns: list[TurnDict] = []
    speaker_map: dict[str, str] = {}

    if ext in (".html", ".htm"):
        pattern = re.compile(
            r'data-speaker="([^"]+)".*?'
            r'data-spk-label="[^"]*">(.*?)</span>.*?'
            r'class="timestamp">([^<]+)</span>.*?'
            r'class="text">(.*?)</div>',
            re.DOTALL,
        )
        for m in pattern.finditer(raw_text):
            orig_spk, disp_spk, ts_range, txt_html = m.groups()
            orig_spk = html.unescape(orig_spk).strip()
            disp_spk = html.unescape(disp_spk).strip()
            if disp_spk and disp_spk != orig_spk:
                speaker_map[orig_spk] = disp_spk
            ts_parts = re.split(r"\s*[–\-]\s*", ts_range.strip())
            start_s = parse_time_str(ts_parts[0]) if ts_parts else 0.0
            end_s = parse_time_str(ts_parts[1]) if len(ts_parts) > 1 else start_s
            turns.append(
                {
                    "speaker": orig_spk or "SPEAKER_00",
                    "start": start_s,
                    "end": end_s,
                    "text": sanitize_text(html.unescape(txt_html)),
                }
            )
        if turns:
            return turns, speaker_map
        raw_text = re.sub(r"<[^>]+>", "\n", raw_text)

    if ext == ".json":
        data = json.loads(raw_text)
        if isinstance(data, dict) and "segments" in data:
            return extract_turns_from_whisperx_data(data), {}
        if isinstance(data, dict) and "turns" in data and isinstance(data["turns"], list):
            raw_map = data.get("speaker_map")
            s_map: dict[str, str] = (
                {str(k): str(v) for k, v in raw_map.items()} if isinstance(raw_map, dict) else {}
            )
            return list(data["turns"]), s_map
        if isinstance(data, list):
            cur_t = 0.0
            for item in data:
                if isinstance(item, dict) and item.get("text"):
                    spk = str(item.get("speaker", "SPEAKER_00"))
                    st = float(item.get("start", cur_t))
                    en = float(item.get("end", st + 5.0))
                    cur_t = en
                    turns.append(
                        {"speaker": spk, "start": st, "end": en, "text": sanitize_text(str(item["text"]))}
                    )
            return turns, {}

    if ext in (".srt", ".vtt"):
        blocks = re.split(r"\n\s*\n", raw_text)
        for blk in blocks:
            m_ts = re.search(
                r"(\d{1,2}:\d{2}(?::\d{2})?(?:[.,]\d+)?)\s*-->\s*(\d{1,2}:\d{2}(?::\d{2})?(?:[.,]\d+)?)",
                blk,
            )
            if not m_ts:
                continue
            st = parse_time_str(m_ts.group(1))
            en = parse_time_str(m_ts.group(2))
            lines = [
                ln.strip()
                for ln in blk[m_ts.end() :].splitlines()
                if ln.strip() and not ln.strip().isdigit()
            ]
            txt = sanitize_text(" ".join(lines))
            if not txt:
                continue
            spk = "SPEAKER_00"
            m_spk = re.match(r"^([A-Za-zÆØÅæøå0-9_ ()\-]{2,35}):\s+(.+)$", txt)
            if m_spk:
                spk, txt = m_spk.group(1).strip(), m_spk.group(2).strip()
            turns.append({"speaker": spk, "start": st, "end": en, "text": txt})
        if turns:
            return turns, {}

    cur_t = 0.0
    for line in raw_text.splitlines():
        clean = sanitize_text(line)
        if not clean:
            continue
        m_br = re.match(
            r"^\[(\d{1,2}:\d{2}(?::\d{2})?)\s*[–\-]\s*(\d{1,2}:\d{2}(?::\d{2})?)\]\s*([^:]{1,60}):\s*(.+)$",
            clean,
        )
        if m_br:
            st = parse_time_str(m_br.group(1))
            en = parse_time_str(m_br.group(2))
            spk = m_br.group(3).strip()
            txt = m_br.group(4).strip()
            cur_t = en
            turns.append({"speaker": spk, "start": st, "end": en, "text": txt})
            continue
        m_spk = re.match(r"^([A-Za-zÆØÅæøå0-9_ ()\-]{2,40}):\s+(.+)$", clean)
        if m_spk and not clean.startswith("#"):
            spk = m_spk.group(1).strip()
            txt = m_spk.group(2).strip()
            turns.append({"speaker": spk, "start": cur_t, "end": cur_t + 5.0, "text": txt})
            cur_t += 5.0
        else:
            turns.append({"speaker": "SPEAKER_00", "start": cur_t, "end": cur_t + 5.0, "text": clean})
            cur_t += 5.0
    return turns, speaker_map


def load_turns_from_transcript_file(path: Path) -> tuple[list[TurnDict], dict[str, str]]:
    """Read a transcript file from disk and parse turns + speaker_map."""
    raw_text = path.read_text(encoding="utf-8", errors="replace")
    return parse_transcript_text(raw_text, path.suffix.lower())


_MAX_UNCOMPRESSED_CONTEXT_BYTES = 50 * 1024 * 1024


def _read_zip_member_bounded(zf: zipfile.ZipFile, name: str, remaining_bytes: int) -> bytes:
    """Read a zip member while enforcing a strict upper bound on decompressed bytes."""
    if remaining_bytes <= 0:
        raise ValueError(
            "Utpakket kontekstfil overstiger maks størrelse / Decompressed context exceeds size limit."
        )
    with zf.open(name, "r") as fp:
        data = fp.read(remaining_bytes + 1)
    if len(data) > remaining_bytes:
        raise ValueError(
            "Utpakket kontekstfil overstiger maks størrelse / Decompressed context exceeds size limit."
        )
    return data


def extract_context_from_file(path: Path) -> str:
    """Extract readable text from .pptx, .docx, .odp, .odt, .pdf, .md, .txt, .html, .json, or .csv."""
    ext = path.suffix.lower()
    if ext == ".pptx":
        slides_out: list[str] = []
        remaining = _MAX_UNCOMPRESSED_CONTEXT_BYTES
        with zipfile.ZipFile(path, "r") as zf:
            names = zf.namelist()
            slide_files = sorted(
                (n for n in names if re.match(r"^ppt/slides/slide\d+\.xml$", n)),
                key=lambda s: int(re.search(r"(\d+)", s).group(1)),  # type: ignore[union-attr]
            )
            for idx, s_name in enumerate(slide_files, start=1):
                xml_bytes = _read_zip_member_bounded(zf, s_name, remaining)
                remaining -= len(xml_bytes)
                root = ET.fromstring(xml_bytes)
                paras: list[str] = []
                for p_elem in root.iter():
                    if p_elem.tag.endswith("}p"):
                        runs = [
                            t_elem.text.strip()
                            for t_elem in p_elem.iter()
                            if t_elem.tag.endswith("}t") and t_elem.text and t_elem.text.strip()
                        ]
                        if runs:
                            paras.append(" ".join(runs))
                note_name = f"ppt/notesSlides/notesSlide{idx}.xml"
                if note_name in names:
                    note_bytes = _read_zip_member_bounded(zf, note_name, remaining)
                    remaining -= len(note_bytes)
                    n_root = ET.fromstring(note_bytes)
                    n_runs = [
                        t_elem.text.strip()
                        for t_elem in n_root.iter()
                        if t_elem.tag.endswith("}t") and t_elem.text and t_elem.text.strip()
                    ]
                    if n_runs:
                        paras.append("Foredragsnotat: " + " ".join(n_runs))
                if paras:
                    slides_out.append(f"[Lysbilde {idx}] " + " | ".join(paras))
        return "\n".join(slides_out)

    if ext == ".docx":
        paras_out: list[str] = []
        with zipfile.ZipFile(path, "r") as zf:
            if "word/document.xml" in zf.namelist():
                doc_bytes = _read_zip_member_bounded(
                    zf, "word/document.xml", _MAX_UNCOMPRESSED_CONTEXT_BYTES
                )
                root = ET.fromstring(doc_bytes)
                for p_elem in root.iter():
                    if p_elem.tag.endswith("}p"):
                        runs = [
                            t_elem.text
                            for t_elem in p_elem.iter()
                            if t_elem.tag.endswith("}t") and t_elem.text
                        ]
                        line = "".join(runs).strip()
                        if line:
                            paras_out.append(line)
        return "\n".join(paras_out)

    if ext in (".odp", ".odt"):
        paras_out = []
        with zipfile.ZipFile(path, "r") as zf:
            if "content.xml" in zf.namelist():
                content_bytes = _read_zip_member_bounded(
                    zf, "content.xml", _MAX_UNCOMPRESSED_CONTEXT_BYTES
                )
                root = ET.fromstring(content_bytes)
                for elem in root.iter():
                    if elem.tag.endswith("}p") or elem.tag.endswith("}h"):
                        txt = "".join(elem.itertext()).strip()
                        if txt:
                            paras_out.append(txt)
        return "\n".join(paras_out)

    if ext == ".pdf":
        pdftotext_bin = shutil.which(
            "pdftotext", path="/opt/homebrew/bin:/usr/local/bin:" + os.environ.get("PATH", "")
        )
        if pdftotext_bin:
            try:
                out = subprocess.check_output(
                    [pdftotext_bin, str(path), "-"], text=True, stderr=subprocess.DEVNULL
                )
                if out.strip():
                    return sanitize_text(out)
            except Exception:
                pass
        if sys.platform == "darwin":
            try:
                out = subprocess.check_output(
                    ["mdls", "-raw", "-name", "kMDItemTextContent", str(path)],
                    text=True,
                    stderr=subprocess.DEVNULL,
                )
                if out and out != "(null)" and len(out.strip()) > 20:
                    return sanitize_text(out)
            except Exception:
                pass
        raw_bytes = path.read_bytes()
        extracted_chunks: list[str] = []
        remaining_pdf = _MAX_UNCOMPRESSED_CONTEXT_BYTES
        for m in re.finditer(rb"stream[\r\n]+(.*?)[\r\n]+endstream", raw_bytes, flags=re.DOTALL):
            if remaining_pdf <= 0:
                raise ValueError(
                    "Utpakket PDF-strøm overstiger maks størrelse / Decompressed PDF stream exceeds size limit."
                )
            stream_data = m.group(1)
            try:
                dobj = zlib.decompressobj()
                decompressed = dobj.decompress(stream_data, remaining_pdf + 1)
                if len(decompressed) > remaining_pdf or dobj.unconsumed_tail:
                    raise ValueError(
                        "Utpakket PDF-strøm overstiger maks størrelse / Decompressed PDF stream exceeds size limit."
                    )
                remaining_pdf -= len(decompressed)
            except ValueError:
                raise
            except Exception:
                continue
            for tm in re.finditer(rb"\(([^()\\]*(?:\\.[^()\\]*)*)\)\s*Tj", decompressed):
                s = tm.group(1).decode("latin1", errors="ignore")
                if s.strip():
                    extracted_chunks.append(s)
            for tj_arr in re.finditer(rb"\[(.*?)\]\s*TJ", decompressed, flags=re.DOTALL):
                parts = re.findall(rb"\(([^()\\]*(?:\\.[^()\\]*)*)\)", tj_arr.group(1))
                joined = "".join(p.decode("latin1", errors="ignore") for p in parts)
                if joined.strip():
                    extracted_chunks.append(joined)
        if extracted_chunks:
            return sanitize_text(" ".join(extracted_chunks))

    txt = path.read_text(encoding="utf-8", errors="replace")
    if ext in (".html", ".htm"):
        txt = re.sub(r"<script[\s\S]*?</script>", "", txt, flags=re.IGNORECASE)
        txt = re.sub(r"<style[\s\S]*?</style>", "", txt, flags=re.IGNORECASE)
        txt = html.unescape(re.sub(r"<[^>]+>", " ", txt))
    return sanitize_text(txt)


def normalize_context_url(url: str) -> str:
    """Normalize Google Docs/Slides share links into direct text/pptx export URLs.

    >>> normalize_context_url("https://docs.google.com/document/d/1AbCdEfG/edit?usp=sharing")
    'https://docs.google.com/document/d/1AbCdEfG/export?format=txt'
    >>> normalize_context_url("https://docs.google.com/presentation/d/9XyZ123/edit#slide=id.p")
    'https://docs.google.com/presentation/d/9XyZ123/export/pptx'
    >>> normalize_context_url("https://example.com/agenda.md")
    'https://example.com/agenda.md'
    """
    clean = (url or "").strip()
    m_doc = re.match(r"^https?://docs\.google\.com/document/d/([a-zA-Z0-9_-]+)", clean)
    if m_doc:
        return f"https://docs.google.com/document/d/{m_doc.group(1)}/export?format=txt"
    m_pres = re.match(r"^https?://docs\.google\.com/presentation/d/([a-zA-Z0-9_-]+)", clean)
    if m_pres:
        return f"https://docs.google.com/presentation/d/{m_pres.group(1)}/export/pptx"
    return clean


def _is_disallowed_ip(addr_str: str) -> bool:
    try:
        ip = ipaddress.ip_address(addr_str.split("%", 1)[0])
        return bool(
            not ip.is_global
            or ip.is_private
            or ip.is_loopback
            or ip.is_link_local
            or ip.is_multicast
            or ip.is_reserved
            or ip.is_unspecified
        )
    except ValueError:
        return False


def _validate_public_http_url(url: str) -> None:
    """Reject non-HTTP(S) schemes and loopback/private/link-local addresses (SSRF protection)."""
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme.lower() not in ("http", "https"):
        raise ValueError(f"Ugyldig nettadresse for kontekst / Invalid context URL: {url}")
    host = (parsed.hostname or "").strip().lower()
    if not host:
        raise ValueError(f"Mangler vertsnavn i URL / Missing host in URL: {url}")
    if host in {"localhost", "localhost.localdomain"} or host.endswith((".local", ".internal")):
        raise ValueError(f"Blokkert lokal/intern adresse / Blocked local or internal host: {host}")
    if _is_disallowed_ip(host):
        raise ValueError(f"Blokkert privat/lokal IP-adresse / Blocked non-public IP: {host}")
    try:
        infos = socket.getaddrinfo(host, None, type=socket.SOCK_STREAM)
        for _fam, _stype, _proto, _canon, sockaddr in infos:
            resolved_ip = str(sockaddr[0])
            if _is_disallowed_ip(resolved_ip):
                raise ValueError(
                    f"Blokkert lokal/privat DNS-oppslag / Blocked non-public resolved IP for {host}"
                )
    except socket.gaierror as exc:
        raise ValueError(
            f"Kunne ikke slå opp vertsnavn / Failed to resolve host: {host}"
        ) from exc


def _resolve_url_validator() -> Callable[[str], None]:
    core_mod = sys.modules.get("mote.core")
    if core_mod is not None:
        patched = getattr(core_mod, "_validate_public_http_url", None)
        if callable(patched):
            return patched
    return _validate_public_http_url


class _SafeRedirectHandler(urllib.request.HTTPRedirectHandler):
    def redirect_request(
        self,
        req: urllib.request.Request,
        fp: Any,
        code: int,
        msg: str,
        headers: Any,
        newurl: str,
    ) -> urllib.request.Request | None:
        _resolve_url_validator()(newurl)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def fetch_context_from_url(
    url: str,
    timeout: int = 20,
    max_bytes: int = 25 * 1024 * 1024,
) -> dict[str, Any]:
    """Download agenda, lecture slides, or draft notes from an HTTP/HTTPS URL and extract clean text."""
    validate_url = _resolve_url_validator()
    raw_url = (url or "").strip()
    validate_url(raw_url)

    eff_url = normalize_context_url(raw_url)
    validate_url(eff_url)
    eff_parsed = urllib.parse.urlparse(eff_url)
    req = urllib.request.Request(
        eff_url,
        headers={"User-Agent": "Mozilla/5.0 (compatible; Steno/1.0)"},
    )
    opener = urllib.request.build_opener(_SafeRedirectHandler())
    with opener.open(req, timeout=timeout) as resp:
        ctype = str(resp.headers.get("Content-Type", "")).lower()
        body = resp.read(max_bytes + 1)
    if len(body) > max_bytes:
        raise ValueError(
            f"Kontekstfilen overstiger maks størrelse ({max_bytes // (1024 * 1024)} MB) / "
            f"Context file exceeds {max_bytes // (1024 * 1024)} MB limit."
        )

    ext = Path(eff_parsed.path).suffix.lower()
    if not ext or ext not in CONTEXT_DOC_EXTENSIONS:
        if "presentationml" in ctype or eff_url.endswith("/pptx"):
            ext = ".pptx"
        elif "wordprocessingml" in ctype:
            ext = ".docx"
        elif "application/pdf" in ctype:
            ext = ".pdf"
        elif "opendocument.presentation" in ctype:
            ext = ".odp"
        elif "opendocument.text" in ctype:
            ext = ".odt"
        elif "application/json" in ctype:
            ext = ".json"
        elif "text/plain" in ctype or "text/markdown" in ctype or "format=txt" in eff_url:
            ext = ".txt"
        else:
            ext = ".html"

    with tempfile.TemporaryDirectory(prefix="steno_ctx_url_") as tmp_dir:
        tmp_file = Path(tmp_dir) / f"downloaded_context{ext}"
        tmp_file.write_bytes(body)
        extracted = extract_context_from_file(tmp_file)

    return {
        "ok": bool(extracted),
        "url": raw_url,
        "resolved_url": eff_url,
        "extension": ext,
        "extracted_text": extracted,
        "char_count": len(extracted),
        "text": extracted,
        "source_name": eff_url,
    }
