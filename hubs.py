"""India IT-hub city list + location matching.

Used by the scraper (to search only where you want), by the server (settings
API) and by the dashboard (the checkbox list), so all three agree on names
and aliases.
"""
from __future__ import annotations

import re

# (label shown in the UI, extra spellings that may appear in a job posting)
HUBS: list[tuple[str, tuple[str, ...]]] = [
    ("Bengaluru", ("bangalore", "bengaluru")),
    ("Hyderabad", ("hyderabad", "secunderabad", "hitech city")),
    ("Pune", ("pune", "maharashtra-pune")),
    ("Chennai", ("chennai", "madras")),
    ("Mumbai", ("mumbai", "bombay", "thane", "navi mumbai")),
    ("Gurugram", ("gurugram", "gurgaon")),
    ("Noida", ("noida", "greater noida", "gr. noida")),
    ("Delhi", ("delhi", "new delhi", "noida delhi ncr")),
    ("Kolkata", ("kolkata", "calcutta")),
    ("Ahmedabad", ("ahmedabad", "ahmadabad")),
    ("Kochi", ("kochi", "cochin", "ernakulam")),
    ("Coimbatore", ("coimbatore", "kovai")),
    ("Indore", ("indore",)),
    ("Chandigarh", ("chandigarh", "mohali", "panchkula")),
    ("Jaipur", ("jaipur",)),
    ("Visakhapatnam", ("visakhapatnam", "vizag", "waltair")),
    ("Bhubaneswar", ("bhubaneswar", "bhubaneshwar", "bhuneshwar")),
    ("Berhampur", ("berhampur", "brahmapur", "gajapati")),
    ("Cuttack", ("cuttack",)),
    ("Rourkela", ("rourkela",)),
    ("Nagpur", ("nagpur",)),
    ("Lucknow", ("lucknow",)),
    ("Mysuru", ("mysuru", "mysore")),
    ("Thiruvananthapuram", ("thiruvananthapuram", "trivandrum")),
    ("Vadodara", ("vadodara", "baroda")),
    ("Surat", ("surat",)),
    ("Bhopal", ("bhopal",)),
    ("Ranchi", ("ranchi",)),
    ("Patna", ("patna",)),
    ("Hubballi", ("hubballi", "hubli", "dharwad")),
]

REMOTE_WORDS = ("remote", "work from home", "wfh", "anywhere in india", "work from anywhere")


def hub_names() -> list[str]:
    return [name for name, _ in HUBS]


def aliases(name: str) -> tuple[str, ...]:
    for label, extra in HUBS:
        if label.lower() == name.lower():
            return (label.lower(),) + extra
    return (name.lower(),)


def matches(cities: list[str], location: str) -> bool:
    """True when a job posting sits in one of the selected cities."""
    loc = (location or "").lower()
    if any(w in loc for w in REMOTE_WORDS):
        return True
    if not loc:
        return False
    for c in cities:
        if any(re.search(r"\b" + re.escape(a), loc) for a in aliases(c)):
            return True
    return False


def extract_resume_skills(text: str, dictionary: list[str] | None = None) -> list[str]:
    """Pull known skills out of resume text (case-insensitive, no duplicates)."""
    from pathlib import Path
    import json

    if dictionary is None:
        p = Path(__file__).resolve().parent / "profile" / "resume_profile.json"
        try:
            dictionary = json.loads(p.read_text(encoding="utf-8")).get("skills", [])
        except Exception:
            dictionary = []
    hay = text.lower()
    found: list[str] = []
    for skill in sorted(set(dictionary), key=len, reverse=True):
        s = str(skill).strip()
        if len(s) < 2:
            continue
        if re.search(r"(?<![a-z0-9])" + re.escape(s.lower()) + r"(?![a-z0-9])", hay):
            if s.lower() not in {f.lower() for f in found}:
                found.append(s)
    return found
