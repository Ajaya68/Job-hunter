"""Local dashboard server: browse matches and trigger a scrape from the UI.

    python serve.py            # start on http://127.0.0.1:8787
    python serve.py --port 9000

Safety notes, because this spends real money:
  * binds to 127.0.0.1 only, so nothing outside this machine can reach it;
  * a run must be confirmed after its cost is shown (server re-checks it);
  * one scrape at a time - a second request is rejected, so a double-click
    cannot fire two paid sweeps.
"""
from __future__ import annotations

import argparse
import base64
import json
import subprocess
import sys
import threading
import time
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import ai_screen  # noqa: E402
import dashboard  # noqa: E402
import hubs  # noqa: E402
import hunt  # noqa: E402
import resume_ai  # noqa: E402
import resume_extract  # noqa: E402
import tailor  # noqa: E402
from pathlib import Path  # noqa: E402

LOG_MAX = 6000


class Run:
    """Single-slot scrape runner: at most one sweep in flight."""

    def __init__(self):
        self.lock = threading.Lock()
        self.proc: subprocess.Popen | None = None
        self.lines: list[str] = []
        self.started: float | None = None
        self.finished: float | None = None
        self.exit_code: int | None = None

    @property
    def running(self) -> bool:
        return self.proc is not None and self.proc.poll() is None

    def start(self) -> tuple[bool, str]:
        with self.lock:
            if self.running:
                return False, "a scrape is already running"
            self.lines = ["--- starting scrape ---"]
            self.started = time.time()
            self.finished = None
            self.exit_code = None
            self.proc = subprocess.Popen(
                [sys.executable, "-u", str(HERE / "hunt.py")],
                cwd=str(HERE), stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT, text=True, encoding="utf-8",
                errors="replace", bufsize=1,
            )
            threading.Thread(target=self._pump, daemon=True).start()
            return True, "started"

    def _pump(self):
        proc = self.proc
        try:
            for raw in proc.stdout:
                self.lines.append(raw.rstrip("\n"))
                if len(self.lines) > LOG_MAX:
                    del self.lines[: len(self.lines) - LOG_MAX]
        except Exception:  # noqa: BLE001
            pass
        proc.wait()
        self.exit_code = proc.returncode
        self.finished = time.time()
        self.lines.append(f"--- finished with exit code {proc.returncode} ---")
        try:
            dashboard.build()
        except Exception as e:  # noqa: BLE001
            self.lines.append(f"dashboard rebuild failed: {e}")

    def stop(self) -> tuple[bool, str]:
        with self.lock:
            if not self.running:
                return False, "nothing running"
            self.proc.terminate()
            self.lines.append("--- stop requested ---")
            return True, "stopping"

    def state(self) -> dict:
        return {
            "running": self.running,
            "started": self.started,
            "finished": self.finished,
            "elapsed": round((time.time() - self.started), 1) if self.running and self.started else None,
            "exitCode": self.exit_code,
        }

    def log_since(self, n: int) -> list[str]:
        return self.lines[max(0, n):]


RUN = Run()


def preflight() -> dict:
    """Cost of the next run and whether the budget allows it. No scraping."""
    cfg = hunt.apply_settings(hunt.load_json(hunt.CONFIG_PATH, {}))
    sources = cfg.get("selected_sources") or \
        [s for s in cfg["sources"] if cfg["sources"][s].get("enabled")]
    work, est = hunt.plan(cfg, sources)
    out = {"runs": len(work), "sources": len(sources), "est": round(est, 4),
           "sourceNames": sources,
           "credit": None, "ok": True, "reason": ""}
    try:
        # credit_status() needs the token global, which main() normally sets.
        if not hunt.TOKEN:
            hunt.TOKEN = hunt.api_token()
        credit = hunt.credit_status()
        if not credit:
            out["warning"] = "could not read your Apify credit - run unverified"
    except Exception as e:  # noqa: BLE001
        credit = None
        out["warning"] = f"could not read Apify credit ({e}) - run unverified"
    if credit:
        out["credit"] = {"used": round(credit["used"], 2), "cap": credit["cap"],
                         "remaining": round(credit["remaining"], 2),
                         "cycleEnd": str(credit["cycle_end"])[:10]}
        if est > credit["remaining"]:
            out["ok"] = False
            out["reason"] = (f"needs ~${est:.3f} but only "
                             f"${credit['remaining']:.2f} remains this cycle")
    return out


def data_status() -> dict:
    """Sizes and age buckets of everything this project accumulates on disk."""
    raw_dir = dashboard.OUT_DIR / "raw"
    raw_files = sorted(raw_dir.glob("*.json")) if raw_dir.exists() else []
    now = time.time()
    ages = [max(0.0, (now - f.stat().st_mtime) / 86400) for f in raw_files]
    try:
        seen = hunt.load_json(hunt.STATE_PATH, {})
    except Exception:
        seen = {}

    def size(p: Path) -> int:
        try:
            return p.stat().st_size
        except Exception:
            return 0

    reports = [f for f in dashboard.OUT_DIR.glob("*.json")] +               [f for f in dashboard.OUT_DIR.glob("*.md")]
    return {
        "raw": {
            "count": len(raw_files),
            "bytes": sum(size(f) for f in raw_files),
            "older7": sum(1 for a in ages if a > 7),
            "older30": sum(1 for a in ages if a > 30),
            "oldest": round(max(ages), 1) if ages else 0,
        },
        "seen": {"count": len(seen)},
        "reports": {"count": len(reports), "bytes": sum(size(f) for f in reports)},
        "dashboardBytes": size(dashboard.OUT_DIR / "dashboard.html"),
    }


def delete_data(body: dict) -> dict:
    """Delete a data section. Requires an explicit scope, never a blanket wipe."""
    scope = str(body.get("scope") or "")
    older = body.get("olderDays")

    def target_ages(f: Path) -> float:
        return max(0.0, (time.time() - f.stat().st_mtime) / 86400)

    removed = 0
    if scope == "raw":
        raw_dir = dashboard.OUT_DIR / "raw"
        for f in sorted(raw_dir.glob("*.json")):
            if older is None or target_ages(f) >= float(older):
                try:
                    f.unlink()
                    removed += 1
                except Exception:
                    pass
    elif scope == "ledger":
        # Clearing the ledger only affects future dedup; it does not touch
        # job postings or any Apify data.
        hunt.save_json(hunt.STATE_PATH, {})
        removed = -1
    elif scope == "reports":
        for pat in ("*.json", "*.md"):
            for f in dashboard.OUT_DIR.glob(pat):
                if f.name != "dashboard.html":
                    try:
                        f.unlink()
                        removed += 1
                    except Exception:
                        pass
    else:
        # Refuse anything not explicitly whitelisted, so a malformed or
        # mistyped request can never turn into a broad delete.
        return {"ok": False, "error": f"unknown or unsupported scope: {scope!r}"}

    try:
        dashboard.build()
    except BaseException:  # noqa: BLE001 - SystemExit when no report remains
        pass
    return {"ok": True, "scope": scope, "removed": removed,
            "status": data_status()}


def _save_resume(text: str, name: str, fname: str | None,
                 profile: dict | None = None, source: str = "") -> dict:
    """Store raw text + Ollama-split profile + the skills the scorer uses.

    The model only fills the sections; it never replaces the raw text, so a
    bad parse can be fixed by hand in the dialog and saved again.
    """
    import datetime as dt
    raw = (text or "").strip()
    if profile is None and raw:
        profile = resume_ai.parse(raw)      # None when Ollama is off/unhappy
    if profile is None:
        profile = hunt.load_resume().get("profile") or resume_ai.empty_profile()
    body_text = resume_ai.to_text(profile) if any(
        profile.get(k) for k in resume_ai.LIST_FIELDS) or profile.get("summary")         else raw
    if profile.get("summary") and raw and resume_ai.to_text(profile) not in raw:
        # the rewritten summary is the model's, keep the raw description too
        body_text = resume_ai.to_text(profile)
    skills = list(profile.get("skills") or [])
    for s in hubs.extract_resume_skills(raw) + hubs.extract_resume_skills(body_text):
        if s.lower() not in [x.lower() for x in skills]:
            skills.append(s)
    rec = {
        "skills": skills[:40],
        "chars": len(raw),
        "text": body_text[:tailor.MAX_RESUME_CHARS],
        "rawText": raw[:tailor.MAX_RESUME_CHARS],
        "profile": profile,
        "name": profile.get("name") or name or "",
        "source": source,
        "uploadedAt": dt.datetime.now().isoformat(timespec="seconds"),
    }
    if fname:
        rec["file"] = fname
    hunt.save_json(hunt.RESUME_PATH, rec)
    return rec


def _hub_from_text(t: str) -> str | None:
    """Name the IT hub a free-text location means, or None."""
    t = (t or "").lower().strip()
    if not t:
        return None
    for name in hubs.hub_names():
        als = [name.lower()] + list(hubs.aliases(name))
        if name.lower() == t or any(a == t or t in a or a in t for a in als if len(a) > 3):
            return name
    return None


def _adopt_resume_city(rec: dict) -> dict:
    """Adopt the resume's city when the user picked none. Instant, no model.

    Query planning used to run inside the save request, but on a CPU-only
    box it takes minutes and the browser aborts the save waiting for it -
    the save had succeeded, yet the user saw a failure. Planning now happens
    right after, via /api/queries/plan; this keeps just the instant part.
    """
    st = hunt.load_settings()
    cities = [c for c in (st.get("cities") or []) if isinstance(c, str)]
    if not cities:
        hit = _hub_from_text((rec.get("profile") or {}).get("location") or "")
        if hit:
            cities = [hit]
            st["cities"] = cities
            hunt.save_json(hunt.SETTINGS_PATH, st)
    return {"planned": False, "queries": st.get("queries") or [],
            "cities": cities}


def _find_job(key: str) -> dict | None:
    rep = dashboard.latest_report()
    if not rep or not key:
        return None
    try:
        d = json.loads(rep.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return None
    for j in d.get("jobs", []) + d.get("previouslyShown", []):
        if j.get("key") == key:
            return j
    return None


def empty_page(msg: str) -> bytes:
    """Minimal page shown when there is no report to render yet."""
    import html as _h
    return ("<!DOCTYPE html><meta charset=utf-8>"
            "<title>Job Hunt</title>"
            "<body style='background:#0f1419;color:#e8eef5;font:15px system-ui;"
            "padding:60px;text-align:center'>"
            "<h1 style='font-size:20px'>No job data yet</h1>"
            f"<p style='color:#93a3b5'>{_h.escape(msg)}</p>"
            "<p style='color:#93a3b5'>Press <b>Run scrape</b> to fetch your first "
            "results.</p></body>").encode("utf-8")


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *a):  # keep the console clean
        pass

    def _send(self, code: int, body: bytes, ctype: str):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _body(self):
        try:
            n = int(self.headers.get("Content-Length") or 0)
            return json.loads(self.rfile.read(n) or b"{}")
        except Exception:
            return None

    def _json(self, obj, code=200):
        self._send(code, json.dumps(obj).encode("utf-8"), "application/json")

    def do_GET(self):  # noqa: N802
        p = urlparse(self.path).path
        if p == "/api/settings":
            return self._json(hunt.load_settings() | {"hubs": hubs.hub_names()})
        if p == "/api/resume":
            return self._json(hunt.load_resume())
        if p == "/api/ai":
            try:
                import ai_screen
                return self._json({"available": ai_screen.available(),
                                   "model": ai_screen.MODEL,
                                   "url": ai_screen.OLLAMA})
            except Exception as e:  # noqa: BLE001
                return self._json({"available": False, "error": str(e)})
        if p in ("/", "/index.html"):
            try:
                dashboard.build()
            except SystemExit as e:
                # No report yet (deleted or never run): still serve the page so
                # the user can press Run rather than seeing a raw error.
                self._send(200, empty_page(str(e)), "text/html; charset=utf-8")
                return
            body = (dashboard.OUT_DIR / "dashboard.html").read_bytes()
            return self._send(200, body, "text/html; charset=utf-8")
        if p == "/api/state":
            return self._json(RUN.state())
        if p == "/api/data":
            return self._json(data_status())
        if p == "/api/log":
            try:
                n = int(urlparse(self.path).query.replace("since=", "") or 0)
            except ValueError:
                n = 0
            return self._json({"lines": RUN.log_since(n), "state": RUN.state()})
        if p == "/api/jobs":
            rep = dashboard.latest_report()
            if not rep:
                return self._json({"jobs": [], "previouslyShown": [], "stats": {}})
            d = json.loads(rep.read_text(encoding="utf-8"))
            return self._json({"jobs": d.get("jobs", []),
                               "previouslyShown": d.get("previouslyShown", []),
                               "stats": d.get("stats", {}),
                               "freshnessDays": d.get("freshnessDays"),
                               "minScore": d.get("minScore"),
                               "generatedAt": d.get("generatedAt")})
        return self._send(404, b"not found", "text/plain")

    def do_POST(self):  # noqa: N802
        p = urlparse(self.path).path
        if p == "/api/preflight":
            try:
                return self._json(preflight())
            except Exception as e:  # noqa: BLE001
                return self._json({"ok": False, "reason": str(e)}, 500)
        if p == "/api/run":
            # Re-check the budget server-side: the button is not the guard.
            try:
                pf = preflight()
            except Exception as e:  # noqa: BLE001
                return self._json({"ok": False, "error": str(e)}, 500)
            if not pf["ok"]:
                return self._json(
                    {"ok": False,
                     "error": f"Refused: this run {pf['reason']}. Trim config.json "
                              f"or wait for the next billing cycle."}, 409)
            ok, msg = RUN.start()
            return self._json({"ok": ok, "message": msg, "preflight": pf},
                              200 if ok else 409)
        if p == "/api/stop":
            ok, msg = RUN.stop()
            return self._json({"ok": ok, "message": msg}, 200 if ok else 409)
        if p == "/api/data":
            return self._json(data_status())
        if p == "/api/data/delete":
            return self._json(delete_data(self._body() or {}))
        if p == "/api/settings":
            body = self._body()
            if not isinstance(body, dict):
                return self._json({"ok": False, "error": "expected an object"}, 400)
            cities = body.get("cities")
            if cities is not None and not isinstance(cities, list):
                return self._json({"ok": False, "error": "cities must be a list"}, 400)
            cur = hunt.load_settings()
            if cities is not None:
                known = set(hubs.hub_names())
                cur["cities"] = [str(c) for c in cities if str(c) in known]
            # every other dashboard control: validated, clamped, stored
            for key, lo, hi in (("minScore", 50, 100),
                                ("maxExperienceYears", 1, 5),
                                ("freshnessDays", 1, 30)):
                v = body.get(key)
                if isinstance(v, bool) or not isinstance(v, (int, float)):
                    continue
                cur[key] = int(min(hi, max(lo, v)))
            if isinstance(body.get("aiScreening"), bool):
                cur["aiScreening"] = body["aiScreening"]
            if isinstance(body.get("aiQueryPlan"), bool):
                cur["aiQueryPlan"] = body["aiQueryPlan"]
            srcs = body.get("sources")
            if isinstance(srcs, list):
                # which scrapers the next run may use; validated against
                # config.json so a stale name can never break a run
                known = set(hunt.load_json(hunt.CONFIG_PATH, {}).get("sources", {}))
                cur["sources"] = [str(s) for s in srcs if str(s) in known]
            hunt.save_json(hunt.SETTINGS_PATH, cur)
            return self._json({"ok": True, "settings": cur, "hubs": hubs.hub_names()})
        if p == "/api/queries/plan":
            # Preview what Ollama would search for - no Apify call, no cost.
            if not ai_screen.available():
                return self._json({"ok": False, "error":
                                   "Ollama is not reachable on "
                                   f"{ai_screen.OLLAMA} - start it with "
                                   "'ollama serve'"}, 503)
            cfg = hunt.apply_settings(hunt.load_json(hunt.CONFIG_PATH, {}))
            qs = ai_screen.plan_queries({
                "cities": cfg.get("target_cities") or [],
                "max_years": int(cfg.get("max_experience_years", 3)),
                "resume_skills": cfg.get("resume_skills") or [],
                "min_score": cfg.get("min_score", 70),
            })
            if not qs:
                return self._json({"ok": False, "error":
                                   "the model returned no usable queries - "
                                   "the run falls back to config.json"})
            cur = hunt.load_settings()
            cur["queries"] = qs
            hunt.save_json(hunt.SETTINGS_PATH, cur)
            return self._json({"ok": True, "queries": qs})
        if p == "/api/resume":
            body = self._body()
            if not isinstance(body, dict):
                return self._json({"ok": False, "error": "expected an object"}, 400)
            text = str(body.get("text") or "")
            if len(text.strip()) < 40:
                return self._json(
                    {"ok": False, "error": "resume text is too short - paste at "
                     "least a few lines"}, 400)
            if not hubs.extract_resume_skills(text):
                return self._json(
                    {"ok": False, "error": "no known skills found in that text - "
                     "check it is a resume, not an empty paste"}, 400)
            rec = _save_resume(text, str(body.get("name") or ""), None,
                               source="paste")
            return self._json({"ok": True, "resume": rec,
                               "profile": rec.get("profile") or {},
                               "auto": resume_ai.has_content(rec.get("profile")),
                               "search": _adopt_resume_city(rec)})
        if p == "/api/resume/file":
            # Same shape as /api/resume, but the bytes come from a real file
            # (pdf/docx/tex/...) and are decoded by resume_extract first.
            body = self._body()
            if not isinstance(body, dict):
                return self._json({"ok": False, "error": "expected an object"}, 400)
            name = str(body.get("name") or "resume")
            try:
                raw = base64.b64decode(str(body.get("data") or ""), validate=True)
            except Exception:
                return self._json({"ok": False, "error":
                                   "file could not be read - try again"}, 400)
            try:
                text = resume_extract.extract(name, raw)
            except ValueError as e:
                return self._json({"ok": False, "error": str(e)}, 400)
            except Exception as e:  # noqa: BLE001
                return self._json({"ok": False, "error":
                                   f"could not open that file type ({e})"}, 400)
            if not hubs.extract_resume_skills(text):
                return self._json(
                    {"ok": False, "error":
                     "opened the file but found no known skills in it - check "
                     "it is really your resume"}, 400)
            rec = _save_resume(text, name, name, source="file")
            return self._json({"ok": True, "resume": rec, "preview": text[:4000],
                               "profile": rec.get("profile") or {},
                               "auto": resume_ai.has_content(rec.get("profile")),
                               "search": _adopt_resume_city(rec)})
        if p == "/api/tailor":
            # Needs the job's full description: read it from the latest report.
            body = self._body()
            if not isinstance(body, dict):
                return self._json({"ok": False, "error": "expected an object"}, 400)
            key = str(body.get("key") or "")
            kind = str(body.get("kind") or "resume")
            job = _find_job(key)
            if not job:
                return self._json({"ok": False, "error":
                                   "that job is no longer in the latest report "
                                   "- run a scrape first"}, 404)
            resume = hunt.load_resume()
            return self._json(tailor.tailor(job, resume.get("text") or "", kind))
        if p == "/api/resume/profile":
            # The user edits the auto-filled sections by hand and saves them.
            body = self._body()
            if not isinstance(body, dict):
                return self._json({"ok": False, "error": "expected an object"}, 400)
            prof = body.get("profile")
            if not isinstance(prof, dict):
                return self._json({"ok": False, "error":
                                   "profile must be an object"}, 400)
            cur = hunt.load_resume()
            # The dialog posts back the 4000-char preview after a file
            # upload; never let that shrink the stored full extraction.
            posted = str(body.get("text") or "")
            kept = str(cur.get("rawText") or cur.get("text") or "")
            text = posted if len(posted) > len(kept) else kept
            prof = resume_ai.normalize(prof)
            # Keep whatever the file actually said; the form only rewrites it.
            merged = resume_ai.normalize(cur.get("profile"))
            for k, v in prof.items():
                merged[k] = v
            saved = _save_resume(text, merged.get("name") or "",
                                 cur.get("file"), profile=merged,
                                 source="manual")
            return self._json({"ok": True, "resume": saved, "profile": merged,
                               "text": saved.get("text", "")[:1200],
                               "search": _adopt_resume_city(saved)})
        if p == "/api/resume/delete":
            hunt.save_json(hunt.RESUME_PATH, {})
            return self._json({"ok": True, "resume": {}})
        if p == "/api/clear-applied":
            return self._json({"ok": True, "message": "applied flags cleared "
                            "(reload to see them gone)"})
        return self._send(404, b"not found", "text/plain")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8787)
    ap.add_argument("--no-open", action="store_true")
    a = ap.parse_args()
    url = f"http://127.0.0.1:{a.port}/"
    srv = ThreadingHTTPServer(("127.0.0.1", a.port), Handler)
    print(f"job dashboard -> {url}   (ctrl-c to stop)")
    try:
        import ai_screen
        up = ai_screen.available()
        print(f"ollama: {'connected - ' + ai_screen.MODEL if up else 'NOT reachable - runs will fall back to regex-only'}")
    except Exception as e:  # noqa: BLE001
        print(f"ollama: unavailable ({e})")
    if not a.no_open:
        threading.Timer(0.8, lambda: webbrowser.open(url)).start()
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\nstopping")
        if RUN.running:
            RUN.stop()
        srv.shutdown()


if __name__ == "__main__":
    main()