"""Offline check: normalise + score the sample payloads already fetched, so we
know date parsing, direct-URL detection and scoring behave before spending money."""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import hunt  # noqa: E402

SAMPLES = [
    ("naukri", Path(__file__).parent / "test/samples/sample_naukri.json"),
    ("linkedin", Path(__file__).parent / "test/samples/sample_linkedin.json"),
    ("wellfound", Path(__file__).parent / "test/samples/sample_wellfound.json"),
]

for source, p in SAMPLES:
    if not p.exists():
        print(f"!! missing {p.name}")
        continue
    raw = json.load(open(p, encoding="utf-8"))
    j = hunt.normalize(raw, source)
    s, hit, miss = hunt.score_job(j)
    print("=" * 70)
    print(f"{source:10s} score={s}")
    print(f"  title    : {j['title'][:70]}")
    print(f"  company  : {j['company'][:50]}")
    print(f"  posted   : {j['posted_at']}  age={j['age_days']}  -> {hunt.rel(j['posted_at'])}")
    print(f"  apply_url: {j['apply_url'][:100] or '(none)'}")
    print(f"  board_url: {j['board_url'][:100] or '(none)'}")
    print(f"  location : {j['location'][:60]}")
    print(f"  exp      : {j['experience'][:40]}  salary: {j['salary'][:40]}")
    print(f"  matched  : {hit}")
    print(f"  gaps     : {miss}")
    print(f"  dedupkey : {hunt.dedup_key(j)[:80]}")
    print(f"  textlen  : {len(j['text'])}")
