"""Per-job tailored resume and cover letter, written by the local Ollama model.

Why: a fresher's fixed resume scores well against a rubric but never perfectly
against one posting's exact wording. This rewrites the *same* truthful facts so
they use the language of the job description - keywords, emphasis, order -
without inventing experience, employers or dates.

    POST /api/tailor  {"key": "<job key>", "kind": "resume" | "cover"}

Everything stays on this machine (Ollama on localhost). Results are cached by
(job, resume version), so re-opening a job is instant.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
CACHE_PATH = HERE / "state" / "tailor_cache.json"
OLLAMA = os.environ.get("OLLAMA_HOST", "http://localhost:11434").replace(
    "0.0.0.0", "localhost").rstrip("/")
MODEL = os.environ.get("JOBHUNTER_MODEL", "qwen3:8b")

MAX_RESUME_CHARS = 9000
MAX_JD_CHARS = 1600

RESUME_SYSTEM = (
    "You are an expert technical resume writer for the Indian job market. "
    "You tailor an existing resume to ONE specific job description.\n"
    "HARD RULES - breaking one makes the output useless:\n"
    "1. NEVER invent employers, job titles, dates, degrees, certifications, "
    "projects or years of experience. Every fact must exist in the source "
    "resume. The candidate is a fresher BCA graduate with NO work experience.\n"
    "2. Rewrite wording, ordering, emphasis and summary to mirror the job "
    "description: put the skills and technologies this posting asks for first, "
    "use its exact terminology where it is honest to do so, and keep skills "
    "the posting values even if it did not mention them.\n"
    "3. Output ONLY clean Markdown for the resume - no preamble, no "
    "explanation, no 'here is', no code fences.\n"
    "4. Keep it one page: Summary (3 lines), Technical Skills (grouped), "
    "Education, Projects, Certifications, Strengths. Use compact bullets.\n"
    "5. Do not add an Experience/Work History section - there is none to add."
)

COVER_SYSTEM = (
    "You write short, specific cover letters for fresher applicants in India.\n"
    "HARD RULES:\n"
    "1. Never claim experience, employers or achievements absent from the "
    "resume. The candidate is a fresher BCA graduate with training and "
    "projects only.\n"
    "2. 150-190 words, plain text, 3 short paragraphs plus a closing line.\n"
    "3. Name the company and role, cite 2-3 specifics from the job "
    "description that the resume actually supports, and say what he built "
    "(Java/Spring Boot/React projects, training at Naresh IT).\n"
    "4. Confident, direct, no fluff ('I am writing to...' is banned), no "
    "bullet lists, no subject line.\n"
    "5. Output ONLY the letter text - no preamble, no code fences."
)


def available() -> bool:
    try:
        urllib.request.urlopen(OLLAMA + "/api/tags", timeout=4).read()
        return True
    except Exception:  # noqa: BLE001
        return False


def _load_cache() -> dict:
    try:
        return json.loads(CACHE_PATH.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return {}


def _save_cache(c: dict):
    try:
        CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
        # keep the newest 200 entries only
        if len(c) > 200:
            keep = sorted(c.items(), key=lambda kv: kv[1].get("at", ""),
                          reverse=True)[:200]
            c = dict(keep)
        CACHE_PATH.write_text(json.dumps(c, indent=1, ensure_ascii=False),
                              encoding="utf-8")
    except Exception:  # noqa: BLE001
        pass


def _key(job: dict, resume_text: str, kind: str) -> str:
    raw = "|".join([
        kind,
        job.get("key", "") or job.get("id", ""),
        hashlib.sha1(resume_text.encode("utf-8", "replace")).hexdigest()[:12],
        hashlib.sha1((job.get("jd") or job.get("text") or "").encode(
            "utf-8", "replace")).hexdigest()[:12],
    ])
    return hashlib.sha1(raw.encode()).hexdigest()[:20]


def _prompt(job: dict, resume_text: str, kind: str) -> str:
    jd = re.sub(r"\s+", " ", (job.get("jd") or job.get("text") or ""))[:MAX_JD_CHARS]
    resume = resume_text.strip()[:MAX_RESUME_CHARS]
    head = (
        f"JOB POSTING\n"
        f"Title: {job.get('title','')}\n"
        f"Company: {job.get('company','') or 'not stated'}\n"
        f"Location: {job.get('location','') or 'not stated'}\n"
        f"Experience asked: {job.get('experience') or 'not stated'}\n"
        f"Skills the posting matches on: {', '.join(job.get('matched') or [])}\n"
        f"Description: {jd or 'not available'}\n\n"
        f"SOURCE RESUME (facts - do not add to it)\n{resume}\n"
    )
    if kind == "cover":
        return COVER_SYSTEM + "\n\n" + head + "\nWrite the cover letter now."
    return RESUME_SYSTEM + "\n\n" + head + "\nWrite the tailored resume now."


def _generate(prompt: str, timeout: int = 300) -> str:
    body = {"model": MODEL, "prompt": prompt, "stream": False, "think": False,
            "options": {"temperature": 0.4, "num_predict": 2200}}
    req = urllib.request.Request(OLLAMA + "/api/generate",
                                 data=json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return (json.load(r).get("response") or "").strip()


def _clean(text: str) -> str:
    t = text.strip()
    t = re.sub(r"^```(?:markdown|md)?\s*|\s*```$", "", t, flags=re.M).strip()
    # drop an assistant's throat-clearing if the model added one anyway
    t = re.sub(r"^(sure|here(?:'s| is)|below is|as requested)[^\n]*\n+",
               "", t, flags=re.I)
    return t.strip()


def tailor(job: dict, resume_text: str, kind: str) -> dict:
    """Return {'ok': bool, 'text': str, 'cached': bool, 'error': str}."""
    if kind not in ("resume", "cover"):
        return {"ok": False, "error": f"unknown kind {kind!r}"}
    if not resume_text or len(resume_text.strip()) < 40:
        return {"ok": False, "error":
                "upload your resume first (Resume button) - the tailor works "
                "from your real resume, not a blank template"}
    if not available():
        return {"ok": False, "error":
                f"Ollama is not reachable on {OLLAMA} - start it with "
                f"'ollama serve' and try again"}

    k = _key(job, resume_text, kind)
    cache = _load_cache()
    hit = cache.get(k)
    if hit and hit.get("text"):
        return {"ok": True, "text": hit["text"], "cached": True, "error": ""}

    import datetime as _dt
    try:
        out = _clean(_generate(_prompt(job, resume_text, kind)))
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": f"model call failed: {e}"}
    if len(out) < 80:
        return {"ok": False, "error":
                "the model returned almost nothing - try again"}
    cache[k] = {"at": _dt.datetime.now().isoformat(timespec="seconds"),
                "kind": kind, "text": out}
    _save_cache(cache)
    return {"ok": True, "text": out, "cached": False, "error": ""}
