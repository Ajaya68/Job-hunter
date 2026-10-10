"""End-to-end check of dedup + report generation using synthetic jobs.

Runs entirely offline, so the output path is verified without spending credit.
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import hunt  # noqa: E402

# Never write into the real out/ directory - this is synthetic data.
TMP_OUT = Path(__file__).resolve().parent / "out" / "_selftest"
hunt.OUT_DIR = TMP_OUT


def mk(title, company, url, age_days, exp, src="naukri", desc="Java Spring Boot Oracle PL/SQL REST API React"):
    when = hunt.NOW - __import__("datetime").timedelta(days=age_days)
    job = {
        "title": title, "company": company, "apply_url": url, "board_url": url,
        "posted_at": when.isoformat(), "age_days": age_days, "salary": "Not Disclosed",
        "experience": exp, "location": "Bhubaneswar", "text": f"{title} {desc}",
        "source": src,
    }
    s, hit, miss = hunt.score_job(job)
    job["score"], job["matched"], job["missing"] = s, hit, miss
    job["key"] = hunt.dedup_key(job)
    return job


JOBS = [
    mk("Full Stack Java Developer", "Acme Corp",
       "https://www.naukri.com/job-listings-full-stack-java-developer-acme-1", 0.4, "0-3 yrs"),
    mk("Java Backend Developer", "Beta Ltd",
       "https://in.linkedin.com/jobs/view/java-backend-developer-beta-2?trk=x", 1.2, "2-4 yrs"),
    mk("Senior Java Developer", "Gamma Inc",
       "https://careers.gamma.com/jobs/3", 2.0, "5-8 yrs"),
    mk("Java Developer", "Delta",
       "https://www.naukri.com/job-listings-java-developer-delta-4", 0.1, "0-2 yrs"),
]

cfg = {"freshness_days": 3, "min_score": 70}
stats = {"naukri": {"raw": 10, "new": 3}, "linkedin": {"raw": 5, "new": 1}}
work = [("naukri", "Java Developer", "Bhubaneswar", 20)]

kept, dropped = [], []
for j in JOBS:
    (dropped if hunt.reject_reason(j) else kept).append(j)

print("gated out:", [(j["title"], hunt.reject_reason(j)) for j in dropped])
print("kept      :", [(j["title"], j["score"]) for j in kept])

# dedup by key, keeping the first occurrence
seen, uniq = set(), []
for j in kept:
    if j["key"] in seen:
        continue
    seen.add(j["key"])
    uniq.append(j)

# near-duplicate collapse (same company + same title)
def near(j):
    import re
    t = re.sub(r"\s+", " ", re.sub(r"[^a-z0-9 ]+", " ", j["title"].lower())).strip()
    c = re.sub(r"[^a-z0-9]+", "", j["company"].lower())
    return f"{c}::{t}"

best = {}
for j in sorted(uniq, key=lambda x: -x["score"]):
    best.setdefault(near(j), j)
final = sorted(best.values(), key=lambda j: (-j["score"], j["age_days"]))

# a second pass with the same input must surface nothing new
second = [j for j in final if j["key"] not in {k for k in seen}]

print(f"\nunique: {len(uniq)}  after near-dup: {len(final)}  rerun new: {len(second)}")

path = hunt.write_report(final, cfg, stats, work, seen_count=len(seen) + len(uniq))
print("report:", path)
text = Path(path).read_text(encoding="utf-8")
print("-" * 60)
print(text[:1400])

fails = []
if dropped and not any(hunt.reject_reason(j) == "senior-only-title" for j in dropped):
    fails.append("senior role was not gated out")
if second:
    fails.append("rerun would re-surface jobs (dedup broken)")
if "Senior" in text:
    fails.append("senior role leaked into the report")
for j in final:
    if j["age_days"] > 3:
        fails.append(f"stale job in report: {j['title']}")

print("=" * 60)
print("FAIL: " + "; ".join(fails) if fails else "all report/dedup checks passed")
sys.exit(1 if fails else 0)
