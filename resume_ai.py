"""Turn a raw resume dump into editable sections with the local model.

Why: uploading a PDF gives you a wall of text, and the dashboard needs
structured fields (name, skills, education, projects) that the user can then
fix by hand. The model does the boring split; the user stays the editor.

    state/resume.json  ->  {"text": <raw>, "profile": {...}, "skills": [...]}

Everything is local (Ollama on localhost). A failure at any point falls back to
the plain-text extract, so an upload never dead-ends.
"""
from __future__ import annotations

import json
import re
import urllib.request

import hubs  # noqa: E402  - shared skill vocabulary

FIELDS = ("name", "email", "phone", "location", "summary")
LIST_FIELDS = ("skills", "education", "projects", "certifications", "strengths")

SYSTEM = (
    "You parse one Indian fresher's resume into exact JSON fields. The owner "
    "is a BCA graduate with no work experience - never invent employers, "
    "dates, degrees or jobs that are not written in the text. "
    "Keep every value SHORT: summary is one sentence of at most 40 words, "
    "each list holds at most 12 skills, 3 projects, 2 education lines, "
    "4 certifications and 6 strengths.\n"
    "Return ONLY valid JSON with these keys:\n"
    '  "name", "email", "phone", "location": strings ("" when absent),\n'
    '  "summary": string, one short professional summary in the candidate\'s '
    "voice, written from what is actually in the resume,\n"
    '  "skills": array of short skill strings (technologies/tools only),\n'
    '  "education": array of strings, one line each '
    '(e.g. "BCA - Berhampur University, 2026 - CGPA 8.5"),\n'
    '  "projects": array of strings, one line each with tech stack, '
    '("Project - Java, Spring Boot: one clause on what it does"),\n'
    '  "certifications": array of strings, one per certificate or training, '
    'including "Naresh IT Java Full Stack" if present,\n'
    '  "strengths": array of short strings (achievements, languages, soft '
    "skills actually listed).\n"
    "Omit nothing: use an empty string or [] where the resume is silent."
)


def available() -> bool:
    try:
        urllib.request.urlopen(_URL() + "/api/tags", timeout=4).read()
        return True
    except Exception:  # noqa: BLE001
        return False


def _URL() -> str:
    import os
    return os.environ.get("OLLAMA_HOST", "http://localhost:11434").replace(
        "0.0.0.0", "localhost").rstrip("/")


def _model() -> str:
    import os
    return os.environ.get("JOBHUNTER_MODEL", "qwen3:8b")


def _parse(text: str) -> dict | None:
    """The model sometimes wraps JSON in prose or fences - tolerate both."""
    if not text:
        return None
    t = re.sub(r"^```(?:json)?|```$", "", text.strip(), flags=re.M)
    try:
        obj = json.loads(t)
        return obj if isinstance(obj, dict) else None
    except Exception:  # noqa: BLE001
        pass
    m = re.search(r"\{.*\}", t, re.S)
    if m:
        try:
            obj = json.loads(m.group(0))
            return obj if isinstance(obj, dict) else None
        except Exception:  # noqa: BLE001
            return None
    return None


def _clean_list(v, cap: int = 30) -> list[str]:
    if isinstance(v, str):
        v = re.split(r"[;\n]+", v)
    if not isinstance(v, list):
        return []
    out = []
    for x in v[:cap]:
        s = re.sub(r"\s+", " ", str(x)).strip(" -•\t")
        if 2 <= len(s) <= 160 and s.lower() not in [y.lower() for y in out]:
            out.append(s)
    return out


def empty_profile() -> dict:
    d = {k: "" for k in FIELDS}
    d.update({k: [] for k in LIST_FIELDS})
    return d


def normalize(obj: dict | None) -> dict:
    """Shape whatever the model returned into a safe profile."""
    p = empty_profile()
    if not isinstance(obj, dict):
        return p
    for k in FIELDS:
        v = obj.get(k)
        if isinstance(v, str):
            p[k] = re.sub(r"\s+", " ", v).strip()[:400 if k == "summary" else 120]
    for k in LIST_FIELDS:
        p[k] = _clean_list(obj.get(k))
    return p


def has_content(profile: dict | None) -> bool:
    """True when Ollama actually recovered something worth showing."""
    if not isinstance(profile, dict):
        return False
    if any((profile.get(k) or "").strip() for k in FIELDS):
        return True
    return any(profile.get(k) for k in LIST_FIELDS)


def _decamel(s: str) -> str:
    """Restore spaces PDF styling ate ("FullStackJava" -> "Full Stack Java").

    Joined words cost the tokenizer far more tokens than spaced prose, and on
    a CPU-only box every token is ~0.4 s of prompt reading. Headers, tech
    terms and names are CamelCase-joined; lowercase-glued prose is left to
    the model, which reads it fine once the prompt fits the time budget.
    """
    s = re.sub(r"(?<=[a-z])(?=[A-Z])", " ", s)
    return re.sub(r"(?<=[A-Z])(?=[A-Z][a-z])", " ", s)


def _shrink(raw: str, cap: int) -> str:
    """Collapse PDF whitespace deserts, then keep the informative head.

    Contact block, summary, skills and the first projects all live in the
    first ~2k characters; if an Education header sits further down, a small
    window around it is appended so the degree is never lost.
    """
    t = _decamel(re.sub(r"\s+", " ", (raw or "")).strip())
    if len(t) <= cap:
        return t
    head = t[:cap]
    m = re.search(r"education", t[cap:], re.I)
    if m:
        start = max(0, cap + m.start() - 120)
        head += " ... " + t[start:start + 700]
    return head


def _bullets(text: str) -> list[str]:
    """Salvage project lines when the model answers in markdown, not JSON."""
    out = []
    for line in (text or "").splitlines():
        s = line.strip()
        m = re.match(r"^(?:###\s+|\d+[.)]\s+|\*\*(.+?)\*\*|[-*]\s+)(.*)$", s)
        if not m:
            continue
        name = (m.group(1) or m.group(2) or "").strip("*#: ").strip()
        if len(name) >= 4 and "technolog" not in name.lower()[:20]:
            out.append(name)
    return out


def fill_projects(raw: str, profile: dict, timeout: int = 300) -> dict:
    """Targeted second pass for the projects list only.

    The full-profile call sometimes spends its whole output budget before
    reaching projects (or skips them to save tokens). This sends just the
    Projects section and asks for at most 3 one-line entries - about two
    minutes of CPU inference - then merges them into the profile.
    """
    try:
        t = _decamel(re.sub(r"\s+", " ", (raw or "")).strip())
        m = re.search(r"projects?", t, re.I)
        if not m:
            return profile
        chunk = t[max(0, m.start() - 100):m.start() + 1800]
        prompt = ("List the software projects in this resume text. "
                  "Reply with ONLY a JSON array of at most 3 strings, "
                  "one line each as \"Name - tech stack: one clause "
                  "on what it does\". No prose, no other keys.\n\n"
                  f"RESUME:\n{chunk}")
        resp = ""
        for attempt in ({"format": "json"}, {}):
            # qwen3's grammar-constrained JSON mode occasionally 500s;
            # the plain retry plus bullet salvage below covers that.
            try:
                req = urllib.request.Request(
                    _URL() + "/api/generate",
                    data=json.dumps({
                        "model": _model(), "prompt": prompt,
                        "stream": False, "think": False,
                        "options": {"temperature": 0.1, "num_predict": 300,
                                    "num_ctx": 4096},
                        **attempt,
                    }).encode(),
                    headers={"Content-Type": "application/json"})
                with urllib.request.urlopen(req, timeout=timeout) as r:
                    resp = json.load(r).get("response", "")
                if resp.strip():
                    break
            except Exception:  # noqa: BLE001
                continue
        obj = _parse(resp)
        if isinstance(obj, list):
            arr = obj
        elif isinstance(obj, dict):
            arr = obj.get("projects")
            if not isinstance(arr, list):
                # qwen answers {"Project - role: stack": "..."} or even
                # {"project A...": "#project B..."} - keys carry the entry,
                # values are appended only when short and not another entry
                pairs = []
                for k, v in obj.items():
                    k = (k or "").strip()
                    if not k:
                        continue
                    v = v.strip() if isinstance(v, str) else ""
                    if v and len(v) < 80 and not v.startswith("#"):
                        pairs.append(f"{k} - {v}")
                    else:
                        pairs.append(k)
                        vv = v.lstrip("#").strip()
                        if vv and len(vv) <= 200:
                            pairs.append(vv)
                arr = pairs
        else:
            arr = None
        got = _clean_list(arr, cap=3) if isinstance(arr, list) else []
        if not got:
            got = _clean_list(_bullets(resp), cap=3)
        if got:
            profile["projects"] = got
    except Exception:  # noqa: BLE001
        pass
    return profile


def parse(raw: str, timeout: int = 420) -> dict | None:
    """Split raw resume text into profile fields. None = model unusable.

    Tries the full head first, then once more with a shorter slice: a
    huge prompt is the usual reason the model stalls past the timeout.
    On this CPU-only machine one attempt takes ~3-4 minutes.
    """
    raw = (raw or "").strip()
    if len(raw) < 60:
        return None
    known = hubs.extract_resume_skills(raw)
    caps = [(2600, {"format": "json"}), (2600, {}), (1500, {})]
    for cap, attempt in caps:
        chunk = _shrink(raw, cap)
        try:
            req = urllib.request.Request(
                _URL() + "/api/generate",
                data=json.dumps({
                    "model": _model(), "prompt": f"{SYSTEM}\n\nRESUME:\n{chunk}",
                    "stream": False, "think": False,
                    "options": {"temperature": 0.1, "num_predict": 600,
                                "num_ctx": 4096},
                    **attempt,
                }).encode(),
                headers={"Content-Type": "application/json"})
            with urllib.request.urlopen(req, timeout=timeout) as r:
                resp = json.load(r).get("response", "")
        except Exception:  # noqa: BLE001
            continue
        p = normalize(_parse(resp))
        # never lose skills the deterministic extractor already knows about
        for s in known:
            if s.lower() not in [x.lower() for x in p["skills"]]:
                p["skills"].append(s)
        if has_content(p):
            if not p["projects"]:
                p = fill_projects(raw, p)   # cheap second pass, see above
            return p
    return None


def to_text(profile: dict) -> str:
    """Profile -> plain text used by the scorer and the tailor prompt."""
    p = normalize(profile)
    lines = []
    if p["name"]:
        lines.append(p["name"])
        bits = [b for b in (p["email"], p["phone"], p["location"]) if b]
        if bits:
            lines.append(" | ".join(bits))
    if p["summary"]:
        lines += ["", p["summary"]]
    if p["skills"]:
        lines += ["", "Skills: " + ", ".join(p["skills"])]
    for title, key in (("Education", "education"), ("Projects", "projects"),
                       ("Certifications", "certifications"),
                       ("Strengths", "strengths")):
        if p[key]:
            lines.append("")
            lines.append(title + ":")
            lines += [f"- {x}" for x in p[key]]
    return "\n".join(lines).strip()
