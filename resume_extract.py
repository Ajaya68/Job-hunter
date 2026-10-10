"""Pull plain text out of a resume file using only the standard library.

No pip installs: PDF (via pypdf if present, otherwise a small built-in
content-stream reader), Word/PowerPoint/ODF (they are zip+XML), RTF, HTML,
LaTeX and plain text.

    from resume_extract import extract
    text = extract("cv.pdf", open("cv.pdf", "rb").read())
"""
from __future__ import annotations

import html as _html
import io
import re
import zipfile
import zlib

MAX_BYTES = 8 * 1024 * 1024

_TEXT_ZIPS = ("word/", "ppt/slides/", "content.xml")


def extract(name: str, data: bytes) -> str:
    """Return the readable text of a resume file. Raises ValueError if empty."""
    if not data:
        raise ValueError("that file is empty")
    if isinstance(data, str):
        data = data.encode("utf-8", "replace")
    if len(data) > MAX_BYTES:
        raise ValueError("file is larger than 8 MB")
    ext = name.rsplit(".", 1)[-1].lower() if "." in name else ""

    if ext == "pdf" or data[:5] == b"%PDF-":
        return _require(_pdf(data), name, "PDF")
    if ext in ("docx", "pptx", "odt", "odp", "ods") or data[:2] == b"PK":
        # the zip magic wins: a .docx renamed to .doc still opens
        return _require(_zip_office(data), name, "Word/PowerPoint/OpenDocument")
    if ext in ("doc", "ppt", "xls"):
        raise ValueError(
            "old binary .doc/.ppt files cannot be read - open it and use "
            "Save As -> .docx, PDF, or just paste the text")
    if ext == "rtf" or data[:5] == b"{\\rtf":
        return _require(_rtf(data), name, "RTF")
    if ext in ("html", "htm") or data.lstrip()[:10].lower() in (b"<!doctype ", b"<html"):
        return _require(_html_text(data), name, "HTML")
    if ext in ("tex", "latex", "ltx", "cls", "sty"):
        return _require(_tex(data), name, "LaTeX")
    return _require(_decode(data), name, "text")


def _require(text: str, name: str, kind: str) -> str:
    text = _clean(text)
    if len(text.strip()) < 40:
        raise ValueError(
            f"could not read text from that {kind} file - it may be a scan or "
            f"image-only. Open it and paste the text instead.")
    return text


def _clean(t: str) -> str:
    t = t.replace("\x00", "").replace("\r\n", "\n").replace("\r", "\n")
    t = "".join(ch if (ch >= " " or ch == "\n") else " " for ch in t)
    t = re.sub(r"[ \t]+", " ", t)
    return re.sub(r"\n{3,}", "\n\n", t).strip()


def _decode(data: bytes) -> str:
    for enc in ("utf-8-sig", "utf-16", "latin-1"):
        try:
            t = data.decode(enc)
        except (UnicodeDecodeError, LookupError):
            continue
        if enc == "utf-16" and t[:40].count("\x00"):
            continue
        return t
    return ""


# ------------------------------------------------------------------- plain
def _tex(data: bytes) -> str:
    t = _decode(data)
    t = re.sub(r"(?m)^\s*%.*$", "", t)               # comments
    t = re.sub(r"\\(begin|end)\{[^}]*\}", " ", t)     # environments
    t = re.sub(r"\\[a-zA-Z]+\*?(\[[^\]]*\])?\{([^}]*)\}", r"\2", t)  # \cmd{x} -> x
    return t


def _html_text(data: bytes) -> str:
    t = _decode(data)
    t = re.sub(r"(?is)<(script|style|head).*?</\1>", " ", t)
    t = re.sub(r"(?i)<(br|/p|/div|/li|/tr|/h[1-6]|/td)[^>]*>", "\n", t)
    t = re.sub(r"<[^>]+>", " ", t)
    return _html.unescape(t)


def _rtf(data: bytes) -> str:
    t = data.decode("latin-1", "replace")
    t = t.replace(r"\line", "\n").replace(r"\par", "\n").replace(r"\tab", "\t")
    t = re.sub(r"\\'([0-9a-fA-F]{2})", lambda m: chr(int(m.group(1), 16)), t)
    t = re.sub(r"\\u(-?\d+)\s?\??", lambda m: chr(int(m.group(1)) & 0xFFFF), t)
    t = re.sub(r"\\[a-zA-Z]+-?\d* ?", " ", t)         # control words
    t = re.sub(r"[{}]", "", t)
    return _html.unescape(t)


# ------------------------------------------------------------- zip formats
def _zip_office(data: bytes) -> str:
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        names = z.namelist()
        wanted = [n for n in names
                  if (n.startswith(_TEXT_ZIPS) or n == "content.xml")
                  and n.endswith(".xml")]
        if not wanted:
            raise ValueError("no document text found inside the file")
        parts = []
        for n in sorted(wanted, key=lambda s: (s != "word/document.xml", s)):
            xml = z.read(n).decode("utf-8", "replace")
            xml = re.sub(r"</(w:p|a:p|text:p|text:h)>", "\n", xml)
            xml = re.sub(r"</w:tc>", "\t", xml)
            xml = re.sub(r"<[^>]+>", "", xml)
            parts.append(_html.unescape(xml))
        return "\n".join(parts)


# -------------------------------------------------------------------- pdf
def _pdf(data: bytes) -> str:
    try:
        from pypdf import PdfReader          # optional speed/accuracy upgrade
    except ImportError:
        try:
            from PyPDF2 import PdfReader
        except ImportError:
            PdfReader = None
    if PdfReader is not None:
        try:
            reader = PdfReader(io.BytesIO(data))
            out = "\n".join((p.extract_text() or "") for p in reader.pages)
            if len(out.strip()) >= 40:
                return out
        except Exception:                    # noqa: BLE001 - fall back below
            pass
    return "\n".join(s for s in (_pdf_streams(d) for d in _pdf_stream_data(data))
                    if s)


def _pdf_stream_data(data: bytes) -> list[bytes]:
    """Every content stream in the file, decompressed where needed."""
    out = []
    for m in re.finditer(rb"stream(?:\r\n|\n|\r)", data):
        start = m.end()
        end = data.find(b"endstream", start)
        if end < 0:
            continue
        raw = data[start:end]
        head_at = data.rfind(b"<<", 0, m.start())
        head = data[head_at:m.start()] if head_at >= 0 else b""
        if any(k in head for k in (b"/DCTDecode", b"/JPXDecode",
                                   b"/CCITTFaxDecode", b"/JBIG2Decode")):
            continue                         # embedded photo, not text
        if b"/FlateDecode" in head:
            try:
                raw = zlib.decompress(raw)
            except zlib.error:
                try:
                    raw = zlib.decompressobj().decompress(raw)
                except zlib.error:
                    continue
        if b"Tj" in raw or b"TJ" in raw or b"BT" in raw:
            out.append(raw)
    return out


def _pdf_streams(buf: bytes) -> str:
    """Read the text-showing operators of one content stream."""
    toks: list[tuple[str, str]] = []
    i, n = 0, len(buf)
    while i < n:
        c = buf[i:i + 1]
        if c == b"(":
            s, i = _pdf_literal(buf, i)
            toks.append(("s", s))
            continue
        if c == b"[" and buf[i:i + 2] != b"[[":
            s, i = _pdf_array(buf, i)
            toks.append(("s", s))
            continue
        if c == b"<" and buf[i:i + 2] != b"<<":
            j = buf.find(b">", i)
            if j < 0:
                break
            hx = re.sub(rb"[^0-9A-Fa-f]", b"", buf[i + 1:j])
            if len(hx) % 2:
                hx += b"0"
            try:
                toks.append(("s", bytes.fromhex(hx.decode()).decode("latin-1")))
            except ValueError:
                pass
            i = j + 1
            continue
        if c == b"'" and i + 1 < n and buf[i - 1:i] in (b"\\", b" ", b"\n", b")"):
            toks.append(("nl", ""))
            i += 1
            continue
        two = buf[i:i + 2]
        if two in (b"Td", b"TD", b"T*", b"ET", b"BT", b"TJ", b"Tj", b"Do"):
            if two != b"TJ" and two != b"Tj":
                toks.append(("nl", ""))
            i += 2
            continue
        i += 1

    out: list[str] = []
    for kind, val in toks:
        if kind == "nl":
            if out and out[-1] != "\n":
                out.append("\n")
        elif val:
            if out and out[-1] not in ("\n", " "):
                out.append(" ")
            out.append(val)
    return "".join(out)


def _pdf_literal(buf: bytes, i: int) -> tuple[str, int]:
    """Read a balanced (string) literal starting at buf[i] == '('."""
    n, j, depth = len(buf), i + 1, 1
    out = bytearray()
    simple = {b"n": 10, b"r": 13, b"t": 9, b"b": 8, b"f": 12,
              b"(": 40, b")": 41, b"\\": 92}
    while j < n:
        ch = buf[j:j + 1]
        if ch == b"\\":
            if j + 1 >= n:
                break
            nx = buf[j + 1:j + 2]
            if nx in simple:
                out.append(simple[nx])
                j += 2
                continue
            if 48 <= nx[0] <= 55:                       # octal \377
                k = j + 1
                while k < min(j + 4, n) and 48 <= buf[k] <= 55:
                    k += 1
                out.append(int(buf[j + 1:k], 8) & 0xFF)
                j = k
                continue
            out += nx                                    # stray escape
            j += 2
            continue
        if ch == b"(":
            depth += 1
        elif ch == b")":
            depth -= 1
            if depth == 0:
                j += 1
                break
        out += ch
        j += 1
    return bytes(out).decode("utf-8", "replace"), j


def _pdf_array(buf: bytes, i: int) -> tuple[str, int]:
    """Read a [ ... TJ ... ] array; the pieces belong to one word run."""
    n, j, depth = len(buf), i + 1, 1
    parts: list[str] = []
    while j < n:
        ch = buf[j:j + 1]
        if ch == b"(":
            s, j = _pdf_literal(buf, j)
            parts.append(s)
            continue
        if ch == b"[":
            depth += 1
        elif ch == b"]":
            depth -= 1
            if depth == 0:
                j += 1
                break
        elif ch == b"<" and buf[j:j + 2] != b"[<":
            k = buf.find(b">", j)
            if k < 0:
                break
            hx = re.sub(rb"[^0-9A-Fa-f]", b"", buf[j + 1:k])
            if len(hx) % 2:
                hx += b"0"
            try:
                parts.append(bytes.fromhex(hx.decode()).decode("latin-1"))
            except ValueError:
                pass
            j = k + 1
            continue
        j += 1
    return "".join(parts), j
