"""Manage local data (raw payloads + seen ledger).

Options:
  --keep-days D        keep raw JSON files newer than D days (default 14)
  --keep-seen-days D   keep ledger entries seen in the last D days (default 60)
  --prune              actually delete, otherwise just show what would be deleted
  --reset-seen         wipe the entire ledger (irreversible)
"""
import argparse, os, shutil
from datetime import datetime, timedelta
from pathlib import Path
import json

HERE = Path(__file__).resolve().parent
RAW = HERE / "out" / "raw"
STATE = HERE / "state" / "seen.json"


def age_days(p: Path) -> float:
    try:
        m = p.stat().st_mtime
        return (datetime.now() - datetime.fromtimestamp(m)).total_seconds() / 86400
    except Exception:
        return 9999


def prune_raw(keep_days: int, dry: bool) -> tuple[int, int]:
    if not RAW.exists():
        return 0, 0
    kept, deld = 0, 0
    for p in RAW.glob("*.json"):
        a = age_days(p)
        if a > keep_days:
            if not dry:
                try:
                    p.unlink()
                    deld += 1
                except Exception:
                    pass
            else:
                deld += 1
        else:
            kept += 1
    return kept, deld


def prune_seen(keep_days: int, dry: bool) -> tuple[int, int]:
    if not STATE.exists():
        return 0, 0
    try:
        d = json.loads(STATE.read_text(encoding="utf-8"))
    except Exception:
        return 0, 0
    cutoff = datetime.now() - timedelta(days=keep_days)
    out, kept, deld = {}, 0, 0
    for k, v in d.items():
        last = v.get("lastSeen") or v.get("firstSeen")
        try:
            t = datetime.fromisoformat(last.replace("Z", "+00:00")).replace(tzinfo=None)
        except Exception:
            t = datetime.fromtimestamp(0)
        if t < cutoff:
            deld += 1
            continue
        out[k] = v
        kept += 1
    if not dry:
        STATE.write_text(json.dumps(out, indent=2, ensure_ascii=False), encoding="utf-8")
    return kept, deld


def main():
    ap = argparse.ArgumentParser(description="Prune raw payloads and seen ledger")
    ap.add_argument("--keep-days", type=int, default=14, help="keep raw < D days")
    ap.add_argument("--keep-seen-days", type=int, default=60, help="keep seen < D days")
    ap.add_argument("--prune", action="store_true", help="actually delete")
    ap.add_argument("--reset-seen", action="store_true", help="wipe seen.json")
    a = ap.parse_args()

    if a.reset_seen:
        if a.prune:
            STATE.write_text("{}", encoding="utf-8")
            print("wiped seen.json")
        else:
            print("(dry-run) would wipe seen.json")
        return

    k, d = prune_raw(a.keep_days, dry=not a.prune)
    print(f"raw: kept={k} deleted={d} (keep < {a.keep_days}d)")
    ks, ds = prune_seen(a.keep_seen_days, dry=not a.prune)
    print(f"seen: kept={ks} deleted={ds} (keep lastSeen < {a.keep_seen_days}d)")


if __name__ == "__main__":
    main()