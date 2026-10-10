"""Local-LLM second opinion on jobs the regex filters cannot judge.

Why this exists: on Indian job boards most postings state no experience band
at all ("not stated" was 16 of 19 jobs in a real run). Regex cannot tell
whether such a posting is a genuine fresher opening or a hidden 5-year role,
so the rules keep it by default and leave you guessing.

This asks a local model instead. It is deliberately a *second opinion*, never
the primary filter:

  * the regex experience gate still decides anything it can decide, instantly;
  * the model is consulted only for jobs with no readable experience band;
  * a job is demoted only when the model is confident, and a model that is
    unreachable or unsure is treated as "keep" - it can never silently hide a
    job, only ever fail to promote one.

No data leaves your machine: this talks to Ollama on localhost.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

HERE = Path(__file__).resolve().parent
CACHE_PATH = HERE / "state" / "ai_cache.json"
OLLAMA = os.environ.get("OLLAMA_HOST", "http://localhost:11434").replace(
    "0.0.0.0", "localhost").rstrip("/")
MODEL = os.environ.get("JOBHUNTER_MODEL", "qwen3:8b")

SYSTEM = (
    "You screen job postings for a fresh BCA graduate applying to entry-level "
    "Java full-stack developer roles. He has 8.5 CGPA, a Naresh IT full-stack "
    "training certificate, and hands-on skills in Java, Spring Boot, Spring Data "
    "JPA, Oracle, PL/SQL, REST/JWT and React 19. He has NO professional work "
    "experience and no internship. Be strict and literal: if a posting implies "
    "any prior professional experience, say no."
)

SCHEMA_HINT = (
    'Answer with ONLY valid JSON and no prose, exactly this shape:\n'
    '{"minimum_years_required": <integer 0-15, or null if genuinely unclear>,'
    ' "fresher_friendly": <true|false>,'
    ' "confidence": <0.0-1.0>,'
    ' "reason": "<max 12 words>"}'
)

_FEWSHOT = """Examples:

Posting: Java Developer, 2-5 Yrs, Spring Boot and Oracle.
{"minimum_years_required": 2, "fresher_friendly": false, "confidence": 0.95, "reason": "requires two years minimum"}

Posting: Graduate Java Developer, 0-2 Yrs, training provided.
{"minimum_years_required": 0, "fresher_friendly": true, "confidence": 0.95, "reason": "explicit graduate trainee opening"}

Posting: Full Stack Java Developer, no experience stated, Spring Boot React Oracle.
{"minimum_years_required": 0, "fresher_friendly": true, "confidence": 0.7, "reason": "no experience bar stated at all"}"""


# ----------------------------------------------------------------- transport
def _post(path: str, body: dict, timeout: int = 180) -> dict:
    req = urllib.request.Request(
        OLLAMA + path, data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.load(r)


def available() -> bool:
    try:
        urllib.request.urlopen(OLLAMA + "/api/tags", timeout=4).read()
        return True
    except Exception:  # noqa: BLE001
        return False


def list_models() -> list[str]:
    try:
        with urllib.request.urlopen(OLLAMA + "/api/tags", timeout=8) as r:
            d = json.load(r)
        return [m.get("name", "") for m in d.get("models", [])]
    except Exception:  # noqa: BLE001
        return []


# -------------------------------------------------------------------- cache
def _load_cache() -> dict:
    try:
        return json.loads(CACHE_PATH.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return {}


def _save_cache(c: dict):
    try:
        CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
        CACHE_PATH.write_text(json.dumps(c, indent=1, ensure_ascii=False),
                              encoding="utf-8")
    except Exception:  # noqa: BLE001
        pass


def _fingerprint(job: dict) -> str:
    """Stable key so an unchanged posting is never re-screened."""
    raw = "|".join([
        job.get("title", ""), job.get("company", ""),
        (job.get("experience") or "").strip(),
        re.sub(r"\s+", " ", job.get("text", ""))[:900],
    ])
    return hashlib.sha1(raw.encode("utf-8", "replace")).hexdigest()[:16]


# ------------------------------------------------------------------- prompt
def build_prompt(job: dict) -> str:
    desc = re.sub(r"\s+", " ", job.get("text", ""))[:1100].strip()
    return (
        f"{SYSTEM}\n\n{_FEWSHOT}\n\n{SCHEMA_HINT}\n\n"
        f"Now screen this posting.\n\n"
        f"Title: {job.get('title','')}\n"
        f"Company: {job.get('company','') or 'not stated'}\n"
        f"Experience field: {job.get('experience') or 'NOT STATED'}\n"
        f"Description: {desc or 'not available'}"
    )


def _parse(text: str) -> dict | None:
    """Tolerant JSON extraction: models occasionally wrap output in prose."""
    if not text:
        return None
    t = text.strip()
    t = re.sub(r"^```(?:json)?|```$", "", t, flags=re.M).strip()
    try:
        return json.loads(t)
    except Exception:  # noqa: BLE001
        pass
    m = re.search(r"\{.*\}", t, re.S)
    if m:
        try:
            return json.loads(m.group(0))
        except Exception:  # noqa: BLE001
            return None
    return None


def judge(job: dict, timeout: int = 180) -> dict:
    """Screen one job. Never raises: uncertainty resolves to 'keep'."""
    verdict = {"fresher_friendly": True, "minimum_years_required": None,
               "confidence": 0.0, "reason": "model unavailable"}
    try:
        d = _post("/api/generate", {
            "model": MODEL,
            "prompt": build_prompt(job),
            "format": "json",       # Ollama constrains output to valid JSON
            "stream": False,
            "think": False,         # skip qwen3 reasoning: 5x faster
            "options": {"temperature": 0, "num_predict": 160,
                        "num_ctx": 2048, "top_p": 0.9},
        }, timeout=timeout)
    except Exception as e:  # noqa: BLE001
        verdict["reason"] = f"model error: {type(e).__name__}"
        return verdict

    obj = _parse(d.get("response", ""))
    if not isinstance(obj, dict):
        verdict["reason"] = "unparseable response"
        return verdict

    ff = obj.get("fresher_friendly")
    conf = obj.get("confidence")
    yrs = obj.get("minimum_years_required")
    verdict = {
        "fresher_friendly": bool(ff) if isinstance(ff, bool) else True,
        "minimum_years_required": yrs if isinstance(yrs, int) else None,
        "confidence": float(conf) if isinstance(conf, (int, float)) else 0.0,
        "reason": str(obj.get("reason") or "")[:90],
    }
    return verdict


# ------------------------------------------------------------------- public
def screen(jobs: list[dict], min_confidence: float = 0.6,
           workers: int = 2, progress=None) -> dict:
    """Screen jobs that the regex gate could not resolve.

    Returns {fingerprint: verdict}. Cached results are reused, so re-running or
    replaying costs nothing after the first pass.
    """
    cache = _load_cache()
    todo, out = [], {}
    for j in jobs:
        fp = _fingerprint(j)
        j["_fp"] = fp
        if fp in cache:
            out[fp] = cache[fp]
            j["ai"] = cache[fp]
        else:
            todo.append(j)

    if todo and not available():
        return out          # unreachable: caller treats every job as "keep"

    def work(j):
        return j, judge(j)

    done = 0
    if todo:
        with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
            for j, v in pool.map(work, todo):
                out[j["_fp"]] = v
                j["ai"] = v
                cache[j["_fp"]] = v
                done += 1
                if progress:
                    progress(done, len(todo), j, v)
                if done % 5 == 0:
                    _save_cache(cache)
    _save_cache(cache)
    return out


def demote(job: dict, min_confidence: float) -> str | None:
    """Reason to drop this job, or None to keep it.

    Conservative by construction: only a clear, confident "no" demotes.
    """
    v = job.get("ai")
    if not v or v.get("fresher_friendly", True):
        return None
    if v.get("confidence", 0.0) < min_confidence:
        return None
    yrs = v.get("minimum_years_required")
    return f"ai-judged-not-fresher: {yrs}yr ({v.get('reason','')})"


# ------------------------------------------------------- query planning
def plan_queries(filters: dict, timeout: int = 120) -> list[str]:
    """Let the local model rewrite the Apify search keywords.

    `filters` is the whole dashboard form: cities, experience ceiling,
    freshness, resume skills, min score. The model turns them into a short
    list of search phrases a job board will actually match on - narrower than
    the generic config queries, so fewer paid results come back.

    Returns [] when the model is unreachable or says nothing useful; the
    caller then falls back to config.json, so this can never break a run.
    """
    if not available():
        return []
    cities = ", ".join(filters.get("cities") or []) or "any city in India"
    skills = ", ".join(filters.get("resume_skills") or []) or "none listed"
    years = filters.get("max_years", 3)
    ask = "0 years - fresher" if years <= 1 else f"0 to {years} years"
    prompt = (
        "You plan search keywords for an Indian job board (Naukri/Indeed) "
        "API. The candidate is a fresher BCA graduate: a Java full-stack "
        "developer with no work experience.\n"
        f"Target roles: entry-level Java developer jobs with {ask} of "
        "experience.\n"
        f"Cities: {cities}.\n"
        f"Resume skills: {skills}.\n"
        "Return ONLY a JSON array of 4 to 6 short search phrases (each under "
        '40 characters, no city names, no quotes inside), e.g. '
        '["java developer fresher", "spring boot trainee"].\n'
        "Favour phrases real fresher postings use (fresher, trainee, junior, "
        "graduate, internship) and the strongest resume skills."
    )
    text = ""
    for attempt in ({"format": "json"}, {}):
        # qwen3's grammar-constrained JSON mode occasionally 500s on this
        # box; the plain retry parses just as well via the tolerant reader.
        try:
            d = _post("/api/generate", {
                "model": MODEL, "prompt": prompt,
                "stream": False, "think": False,
                "options": {"temperature": 0.3, "num_predict": 300},
                **attempt,
            }, timeout=timeout)
            text = d.get("response", "") or ""
            if text.strip():
                break
        except Exception:  # noqa: BLE001
            continue
    if not text.strip():
        return []
    text = text.strip()
    # qwen3 answers with a bare array, an object with one array field, or prose
    # wrapping either - accept all three rather than costing a second call.
    arr = None
    for pat in (r"\[.*\]", r"\{.*\}"):
        m = re.search(pat, text, re.S)
        if not m:
            continue
        try:
            obj = json.loads(m.group(0))
        except Exception:  # noqa: BLE001
            continue
        if isinstance(obj, list):
            arr = obj
            break
        if isinstance(obj, dict):
            for v in obj.values():
                if isinstance(v, list):
                    arr = v
                    break
            if arr is None:
                # qwen sometimes returns {"phrase": "", ...} instead of a list
                ks = list(obj.keys())
                if ks and all(isinstance(k, str) for k in ks):
                    arr = ks
        if arr is not None:
            break
    if arr is None:
        # last resort: the model answered {"phrase": "", ...} but the token
        # cap cut it off mid-dict, so nothing parses - salvage the quoted
        # keys in order, they are the phrases
        keys = re.findall(r'"([^"\\]{3,60})"\s*:', text)
        if keys:
            arr = keys
    if not arr:
        return []
    out = []
    for q in arr if isinstance(arr, list) else []:
        s = re.sub(r"\s+", " ", str(q)).strip().strip('"\'')
        if len(s) < 3 or len(s) > 60:
            continue
        # a fresher plan must never buy senior search results
        if re.search(r"\bsenior|\blead\b|architect|manager|principal|\d\s*\+?\s*yrs", s, re.I):
            continue
        if s.lower() not in [x.lower() for x in out]:
            out.append(s)
    return out[:6]


if __name__ == "__main__":
    import sys
    sys.path.insert(0, str(HERE))
    print("ollama reachable:", available())
    print("models:", list_models() or "none")
    print("using:", MODEL)
    demo = {"title": "Full Stack Java Developer", "company": "Acme",
            "experience": "", "text": "Full Stack Java Developer Spring Boot "
                                       "Oracle PL/SQL REST React microservices"}
    print("\ntest verdict:", json.dumps(judge(demo), indent=1))