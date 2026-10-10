"""
Job hunter: pulls fresh jobs from Apify job scrapers, scores them against a resume
profile, and only surfaces new + high-match + recently-posted roles.

Deduplication is persistent (state/seen.json), so a job surfaced on one day is
never surfaced again on later days.

Usage:
    python hunt.py                 # normal daily run
    python hunt.py --dry-run       # plan runs, print cost estimate, run nothing
    python hunt.py --show-seen     # summary of dedup ledger
    python hunt.py --reset         # wipe dedup ledger (re-surfaces everything)
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import re
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import hubs  # noqa: E402  # shared India IT-hub city list + matching

BASE = Path(__file__).resolve().parent
CONFIG_PATH = BASE / "config.json"
PROFILE_PATH = BASE / "profile" / "resume_profile.json"
STATE_PATH = BASE / "state" / "seen.json"
OUT_DIR = BASE / "out"
RAW_DIR = OUT_DIR / "raw"

API = "https://api.apify.com/v2"
# USD per 1000 results, from each actor's published pay-per-event pricing.
PRICE_PER_1K = {
    "naukri": 0.28,
    "indeed": 0.07,
    "glassdoor": 0.28,
    "linkedin": 1.00,
    "wellfound": 2.79,
}
START_FEE = 0.001


# ----------------------------------------------------------------- token/auth


def api_token() -> str:
    """Read the token the apify CLI already stored in the OS keyring."""
    env = os.environ.get("APIFY_TOKEN")
    if env:
        return env.strip()
    for shell in (True,):
        r = subprocess.run(
            ["apify", "auth", "token"],
            capture_output=True,
            text=True,
            shell=shell,
        )
        lines = [x.strip() for x in r.stdout.splitlines() if x.strip()]
        if lines:
            tok = lines[-1]
            if tok.startswith("apify_api_"):
                return tok
    raise SystemExit("No Apify token found. Run `apify login` first.")


TOKEN = None
HEADERS = {}


def _headers(with_json=False):
    h = {"Authorization": "Bearer " + TOKEN}
    if with_json:
        h["Content-Type"] = "application/json"
    return h


# --------------------------------------------------------------------- helpers


def load_json(path: Path, default):
    if not path.exists():
        return default
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def save_json(path: Path, obj):
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(obj, f, indent=2, ensure_ascii=False)


def flatten(value) -> str:
    """Recursively turn nested dicts/lists (Naukri/Indeed shapes) into text."""
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, (int, float)):
        return str(value)
    if isinstance(value, dict):
        return " ".join(flatten(v) for v in value.values())
    if isinstance(value, list):
        return " ".join(flatten(v) for v in value)
    return str(value)


_TRACKING = re.compile(
    r"(utm_[a-z_]+|gh_src|gclid|fbclid|refId|trackingId|tracking_id|position|"
    r"pageNum|originalSubdomain|indeed-apply-token|iis|iisn|pageNum)",
    re.I,
)


def clean_url(url: str) -> str:
    """Strip tracking params so the dedup key is stable across scrapes."""
    if not url:
        return ""
    try:
        p = urllib.parse.urlsplit(url)
    except ValueError:
        return url
    qs = [(k, v) for k, v in urllib.parse.parse_qsl(p.query, keep_blank_values=True)
          if not _TRACKING.search(k)]
    return urllib.parse.urlunsplit(
        (p.scheme, p.netloc, p.path, urllib.parse.urlencode(qs), "")
    )


NOW = dt.datetime.now(dt.timezone.utc)


def parse_when(value):
    """Best-effort parse of a posting timestamp into an aware UTC datetime."""
    if value is None or value == "":
        return None
    # epoch millis
    if isinstance(value, (int, float)) or (isinstance(value, str) and value.isdigit()):
        n = int(value)
        if n > 1e12:  # millis
            return dt.datetime.fromtimestamp(n / 1000, dt.timezone.utc)
        if n > 1e9:  # seconds
            return dt.datetime.fromtimestamp(n, dt.timezone.utc)
    s = str(value).strip()
    # relative text
    rel = re.match(r"(\d+)\s*(minute|min|hour|hr|day|week|month)s?\s*ago", s, re.I)
    if rel:
        n, unit = int(rel.group(1)), rel.group(2).lower()
        delta = {
            "minute": dt.timedelta(minutes=n), "min": dt.timedelta(minutes=n),
            "hour": dt.timedelta(hours=n), "hr": dt.timedelta(hours=n),
            "day": dt.timedelta(days=n), "week": dt.timedelta(weeks=n),
            "month": dt.timedelta(days=30 * n),
        }[unit]
        return NOW - delta
    m = re.match(r"(\d+)\s*(d|h)\b", s, re.I)
    if m and len(s) < 12:  # "2d", "5h" (Indeed)
        n, unit = int(m.group(1)), m.group(2).lower()
        return NOW - (dt.timedelta(days=n) if unit == "d" else dt.timedelta(hours=n))
    # plain dates / ISO
    t = s.replace("Z", "+00:00")
    for fmt in (None, "%Y-%m-%d %H:%M:%S", "%Y-%m-%d", "%d-%m-%Y", "%b %d, %Y",
                "%d %b %Y", "%Y/%m/%d"):
        try:
            d = dt.datetime.fromisoformat(t) if fmt is None else dt.datetime.strptime(s, fmt)
            return d.replace(tzinfo=dt.timezone.utc) if d.tzinfo is None else d
        except Exception:
            continue
    return None


def age_days(when):
    """Accept a datetime or an ISO string; return age in days or None."""
    if when is None:
        return None
    if isinstance(when, str):
        when = parse_when(when)
        if when is None:
            return None
    return (NOW - when).total_seconds() / 86400.0


def rel(when):
    d = age_days(when)
    if d is None:
        return "unknown"
    if d < 1 / 24:
        return "just now"
    if d < 1:
        return "%dh ago" % int(d * 24)
    return "%dd ago" % int(d)


# ---------------------------------------------------------------- normalisation

# Field-name candidates per source, most specific first.
DATE_FIELDS = [
    "datePublished", "postedAt", "createdDate", "createdDateText", "live_start_at",
    "datePosted", "postedDate", "jobPostedDate", "publishDate", "date",
    "created_at", "createdAt", "timeAgo", "postedDateText", "createdDateISO",
]
DIRECT_URL_FIELDS = [
    "applyUrl", "jobUrl", "apply_url", "externalUrl", "external_url", "atsUrl",
    "directApplyUrl", "companyApplyUrl", "applyLink", "careerUrl", "sourceUrl",
]
BOARD_URL_FIELDS = ["url", "link", "jobUrl", "detailUrl", "job_url", "jobLink", "key"]

BOARD_DOMAINS = (
    "linkedin.com", "indeed.com", "naukri.com", "glassdoor.com", "wellfound.com",
    "angel.co", "shopein.com", "cutshort.io", "foundit.in", "shine.com",
    "internshala.com", "apna.co", "ambitionbox.com", "jobvite.com",
)


def pick(d, names):
    for n in names:
        if d.get(n) not in (None, "", [], {}):
            return d[n]
    return None


def dig(d, paths):
    """Fetch a value by dotted path, so nested label fields beat the parent dict."""
    for p in paths:
        cur = d
        for part in p.split("."):
            if not isinstance(cur, dict):
                cur = None
                break
            cur = cur.get(part)
        if cur not in (None, "", [], {}):
            if isinstance(cur, (dict, list)):
                continue
            return flatten(cur)
    return ""


# Indeed returns no place names at all - only an ISO state code and a lat/lon
# fixpoint. Resolve it offline instead of printing an empty location.
IN_STATE = {
    "AP": "Andhra Pradesh", "BR": "Bihar", "CH": "Chandigarh", "CT": "Chhattisgarh",
    "DL": "Delhi NCR", "GA": "Goa", "GJ": "Gujarat", "HR": "Haryana", "JK": "Jammu",
    "JH": "Jharkhand", "KA": "Karnataka", "KL": "Kerala", "MH": "Maharashtra",
    "MP": "Madhya Pradesh", "OD": "Odisha", "PB": "Punjab", "RJ": "Rajasthan",
    "SK": "Sikkim", "TN": "Tamil Nadu", "TS": "Telangana", "UK": "Uttarakhand",
    "UP": "Uttar Pradesh", "WB": "West Bengal",
}

# (city, lat_min, lat_max, lon_min, lon_max) - a point inside the box is that metro.
METRO_BOXES = [
    ("Bengaluru", 12.75, 13.20, 77.40, 77.85), ("Hyderabad", 17.20, 17.60, 78.20, 78.65),
    ("Chennai", 12.90, 13.25, 79.90, 80.35), ("Coimbatore", 10.95, 11.20, 76.90, 77.15),
    ("Madurai", 9.90, 10.15, 78.05, 78.30), ("Mumbai", 18.85, 19.30, 72.75, 73.05),
    ("Pune", 18.45, 18.75, 73.75, 74.05), ("Nashik", 19.80, 20.10, 73.70, 74.00),
    ("Ahmedabad", 22.95, 23.35, 72.35, 72.85), ("Gurugram", 28.35, 28.75, 76.90, 77.30),
    ("Delhi", 28.40, 28.90, 76.90, 77.35), ("Noida", 28.35, 28.60, 77.20, 77.45),
    ("Kolkata", 22.40, 22.70, 88.20, 88.60), ("Patna", 25.50, 25.70, 84.95, 85.25),
    ("Bhubaneswar", 20.20, 20.45, 85.75, 86.00), ("Chandigarh", 30.65, 30.95, 75.30, 75.60),
    ("Ludhiana", 30.85, 31.05, 75.75, 75.95), ("Jaipur", 26.80, 27.05, 75.70, 76.05),
    ("Indore", 22.60, 22.85, 75.75, 76.05), ("Kochi", 9.90, 10.20, 76.20, 76.45),
    ("Thiruvananthapuram", 8.45, 8.65, 76.90, 77.10), ("Visakhapatnam", 17.65, 17.80, 83.20, 83.35),
]


def _geo_place(raw) -> str:
    """Turn a bare lat/lon + state code into a city a human recognises."""
    lat, lon = raw.get("latitude"), raw.get("longitude")
    try:
        lat, lon = float(lat), float(lon)
        if 0 < lat < 40 and 60 < lon < 100:  # plausibly India
            for city, la0, la1, lo0, lo1 in METRO_BOXES:
                if la0 <= lat <= la1 and lo0 <= lon <= lo1:
                    return city
    except (TypeError, ValueError):
        pass
    for key in ("admin1Code", "state", "stateCode", "region", "regionName"):
        code = str(raw.get(key) or "").strip().upper()
        if code in IN_STATE:
            return IN_STATE[code]
    return ""


def extract_location(raw) -> str:
    """Human-readable place only - never URLs or ids."""
    v = raw.get("location_names")
    if isinstance(v, list) and v:
        return ", ".join(str(x) for x in v if x)
    for key in ("locations", "location"):
        v = raw.get(key)
        if isinstance(v, list):
            labels = [x.get("label") for x in v if isinstance(x, dict) and x.get("label")]
            if labels:
                return ", ".join(labels[:3])
        if isinstance(v, dict):
            for sub in ("formatted", "location", "city", "addressLocality", "label"):
                if v.get(sub):
                    return str(v[sub])[:80]
            # geo-only payloads (Indeed): resolve coordinates to a real place
            place = _geo_place(v)
            if place:
                return place
        if isinstance(v, str) and v and not v.startswith("http"):
            return v[:80]
    for sub in ("jobLocation", "locationText", "locationName", "city"):
        if raw.get(sub):
            return flatten(raw[sub])[:80]
    return _geo_place(raw) or flatten(pick(raw, ["jobLocation", "city", "state"]))[:80]


def normalize(raw: dict, source: str) -> dict:
    """Map any scraper's output onto one canonical job record."""
    title = flatten(pick(raw, ["title", "jobTitle", "position", "primary_role_title", "role"]))

    # Company may be a plain string or a nested object (Naukri/Indeed) - read
    # the name out of the object before flattening, or we lose the mapping and
    # end up printing the raw numeric fields.
    company = pick(raw, ["companyName", "company_name", "employer", "company",
                         "hiringCompany", "organization", "hiring_company"])
    if isinstance(company, dict):
        company = next((company.get(k) for k in
                        ("name", "companyName", "title", "text", "label")
                        if isinstance(company.get(k), str) and company.get(k).strip()), "")
        if not company:
            company = dig(raw, ["employerAttributes.companyName",
                                "companyDetails.name", "hiringCompany.name"])
    company = flatten(company) or ""
    company = re.sub(r"^[\W\d\s]+|[\W\d\s]+$", "", company).strip()
    if not company or not re.search(r"[A-Za-z]{2}", company):
        company = ""

    desc = flatten(pick(raw, [
        "descriptionText", "description", "descriptionHtml", "jobDescription",
        "descriptionSnippet", "description_snippet", "details", "jobDescriptionText",
    ]))
    skills_blob = flatten(pick(raw, [
        "skills", "keySkills", "attributes", "technologies", "skillsRequired", "tags",
    ]))

    when = None
    for f in DATE_FIELDS:
        if raw.get(f) not in (None, "", 0):
            when = parse_when(raw[f])
            if when:
                break
    # Wellfound hands us a ready-made day count
    if when is None and raw.get("live_start_at_days_ago") not in (None, ""):
        try:
            when = NOW - dt.timedelta(days=float(raw["live_start_at_days_ago"]))
        except Exception:
            pass
    # Relative labels embedded in the blob ("2 days ago")
    if when is None:
        m = re.search(r"(\d+\s*(?:minute|min|hour|hr|day|week|month)s?\s*ago)",
                      flatten(raw), re.I)
        if m:
            when = parse_when(m.group(1))

    direct = pick(raw, DIRECT_URL_FIELDS) or ""
    board = pick(raw, BOARD_URL_FIELDS) or ""
    if isinstance(direct, dict):
        direct = flatten(direct)
    if isinstance(board, dict):
        board = flatten(board)
    direct = str(direct or "").strip()
    board = str(board or "").strip()

    def is_board(u):
        return any(d in u.lower() for d in BOARD_DOMAINS)

    # A "direct" link that actually points back at the board is not direct.
    if direct and is_board(direct):
        direct = ""
    apply_url = direct or ("" if is_board(board) else board)

    salary = dig(raw, ["salary.label", "salaryLabel", "salaryText", "salary",
                       "baseSalary", "compensation.label", "compensationText",
                       "compensation", "pay"])

    exp = dig(raw, ["experience.text", "experience.label", "experienceText",
                    "experienceLevel", "seniorityLevel", "yearsExperience",
                    "experience", "exp"])

    loc = extract_location(raw)

    # Match on the role's own words only. The full scraped page carries company
    # boilerplate, benefits lists and "similar jobs" sidebars, which otherwise
    # guarantee a keyword hit for every posting.
    desc_head = re.sub(r"<[^>]+>", " ", desc)[:3000]
    text = " ".join([title, desc_head, skills_blob])
    text = re.sub(r"\s+", " ", text)

    return {
        "source": source,
        "id": flatten(pick(raw, ["id", "jobId", "refNum", "refId", "trackingId", "slug"])),
        "title": title.strip(),
        "company": company.strip()[:120],
        "apply_url": clean_url(apply_url),
        "board_url": clean_url(board),
        "posted_at": when.isoformat() if when else None,
        "age_days": age_days(when),
        "salary": re.sub(r"\s+", " ", salary)[:80],
        "experience": re.sub(r"\s+", " ", exp)[:60],
        "location": re.sub(r"\s+", " ", loc)[:80],
        "text": text[:6000],
    }


# --------------------------------------------------------------------- matching
# --- matching -----------------------------------------------------------------
# A resume for a fresher Java developer. The scorer first applies hard gates
# (reject outright), then scores survivors out of 100.

# Evidence that this is actually a Java backend role.
JAVA_SIGNS = re.compile(
    r"\b(java|j2ee|spring\s*boot|springboot|spring\s*framework|spring\s*mvc|"
    r"spring\s*data|spring\s*security|hibernate|jpa|servlet|jsp|struts|"
    r"core\s*java|jdbc|ma ven|maven|collections?|multithreading)\b", re.I)

# Roles that are a different technology or a different profession entirely.
OFFTRACK_TITLE = re.compile(
    r"\.net\b|dotnet|c#\b|vb\.?net|php\b|laravel|wordpress|drupal|salesforce|"
    r"abap|sap\s*(abap|hana|btc|scm)|\bui\b|ux\b|frontend[- ]only|"
    r"react\s*native|android|ios\b|mobile\s*(app\s*)?dev|flutter|"
    r"machine\s*learning|data\s*(scientist|science|engineer|analyst|warehousing)|"
    r"devops|sre\b|site\s*reliability|cloud\s*architect|network|cyber|security\s*engineer|"
    r"qa\b|quality\s*(assurance|analyst|engineer)|test(er|ing)?\s*engineer|"
    r"hardware|firmware|embedded|vlsi|chip|electronics|mechanical|electrical|"
    r"business\s*analyst|product\s*owner|scrum\s*master|"
    r"account|finance|accounts|tax|audit|banking\s*officer|"
    r"recruit|talent\s*acquisition|human\s*resources|\bhr\s*executive|"
    r"marketing|sales\s*(exec|manager|rep)|business\s*development|"
    r"teacher|faculty|trainer|instructor|content\s*writer|copywriter|seo\b|"
    r"graphic\s*designer|ui/ux|ux\b|video\s*editor|photograph|"
    r"customer\s*support|technical\s*support|help\s*desk|"
    r"intern|trainee|apprentice|summer\s*intern",
    re.I)

# Senior-only signals. A fresher should not be shown these.
SENIOR_ONLY = re.compile(
    r"\b(senior|sr\.?|lead|principal|staff|architect|manager|head\s+of|"
    r"director|vp|chief|ii|iii|iv|v|expert|specialist)\b", re.I)
# ...unless the employer explicitly says they take juniors at this level.
JUNIOR_SIGNAL = re.compile(
    r"\b(junior|jr\.?|associate|entry[- ]level|trainee|fresher|graduate|"
    r"0\s*-\s*\d|0\s*to\s*\d|0\s*-\s*2|0\s*-\s*3)\b", re.I)
# Roman-numeral levels are always the primary level of the role, wherever they sit.
ROMAN_LEVEL = re.compile(r"\b(ii|iii|iv|vi|vii|viii)\b", re.I)


def is_senior_only(title: str) -> bool:
    """True when the posting is aimed at experienced hires only.

    Deliberately strict: a senior word anywhere in the title disqualifies the
    role, because the only reliable evidence that a posting also accepts
    juniors is an explicit junior/fresher word in the title itself. Titles like
    "Java Developer/Senior Java Developer" read as senior-first, so they are
    excluded rather than risking an off-level recommendation.
    """
    if ROMAN_LEVEL.search(title):
        return True
    if not SENIOR_ONLY.search(title):
        return False
    return not JUNIOR_SIGNAL.search(title)

DEGREE_GATE = re.compile(
    r"\b(ph\.?d|doctorate|m\.?tech|mtech|masters?\s+degree|master's|msc\b|mca\b|"
    r"m\.sc\b|m\.ca\b)\b", re.I)

INFRA = re.compile(
    r"\b(kubernetes|k8s|helm|terraform|\baws\b|\bazure\b|\bgcp\b|microservices?|"
    r"service\s*mesh|istio|kafka|spark|hadoop)\b", re.I)

# Roles whose headline specialism is not software engineering with Java.
AI_TITLE = re.compile(
    r"\b(ai|artificial\s+intelligence|machine\s+learning|\bml\b|genai|gen\s*ai|"
    r"llm|large\s+language\s+model|prompt\s*engineering|deep\s+learning|"
    r"data\s*science|nlp|computer\s+vision)\b", re.I)

ROLE_TITLE = re.compile(
    r"\b(full\s*stack|fullstack|backend|back[- ]end|java|software|web|"
    r"application|api|programmer|developer|engineer)\b", re.I)

# Weighted rubric, 100 points.
RUBRIC = [
    ("title-role",   30, ["java", "full stack", "fullstack", "backend", "back-end",
                          "software", "web developer", "application developer",
                          "developer", "engineer", "programmer"]),
    ("java-stack",   22, ["spring boot", "springboot", "spring framework", "spring mvc",
                          "spring data", "spring security", "spring", "hibernate", "jpa",
                          "jdbc", "servlet", "jsp"]),
    ("db",           12, ["sql", "oracle", "pl/sql", "plsql", "mysql", "postgresql",
                          "database", "hibernate", "jpa"]),
    ("api",          10, ["rest", "restful", "api", "web service", "json", "swagger",
                          "microservice", "controller"]),
    ("frontend",      8, ["react", "javascript", "typescript", "html", "css", "angular",
                          "vue", "bootstrap"]),
    ("tooling",       6, ["git", "github", "maven", "docker", "jenkins", "flyway",
                          "swagger", "postman", "intellij", "eclipse", "vs code"]),
    ("domain",        6, ["erp", "inventory", "billing", "purchase", "sales", "report",
                          "financial", "transaction", "payroll", "sql/plsql", "erp system"]),
]


FRESH_WORD = re.compile(
    r"\bfresh(er)?\b|entry[- ]level|graduate|no\s+experience|"
    r"0\s*[-–]?\s*\d*\s*(?:yr|year)s?\b", re.I)


def parse_experience(text: str):
    """Years a posting asks for as (min, max), or None when it states none.

    The MINIMUM is what matters for a fresher: a "3-5 yrs" seat is out of
    reach, while "0-5 yrs" is exactly the kind of posting to surface.
    """
    t = (text or "").strip().lower()
    if not t:
        return None
    m = re.search(r"(\d+)\s*[-–]\s*(\d+)\s*(?:yr|year|yrs|years)?", t)
    if m:
        return int(m.group(1)), int(m.group(2))
    m = re.search(r"(\d+)\s*\+\s*(?:yr|year)", t)
    if m:
        n = int(m.group(1))
        return n, 99          # open-ended: "2+ yrs" means at least 2
    m = re.search(r"(?:up\s*to\s*)?(\d+)\s*(?:yr|year)", t)
    if m:
        n = int(m.group(1))
        return 0, n
    if FRESH_WORD.search(t):
        return 0, 0
    return None


def experience_ok(text: str, max_years: int) -> tuple[bool, str]:
    """Is this posting realistically open to a fresher?

    Two ways to qualify:
      * it asks for at most 1 year, which means a 0-year applicant is welcome
        even when the range is wide ("0-6 yrs" is a classic fresher posting);
      * or the whole range sits within the ceiling ("0-3", "2-3").

    So "2-6 yrs" is dropped: the seat starts at 2 years, which a fresh
    graduate cannot take. Bands the posting never states are kept.
    """
    band = parse_experience(text)
    if band is None:
        return True, ""
    lo, hi = band
    if lo <= 1 or hi <= max_years:
        return True, ""
    return False, f"needs-{lo}-{hi}-yrs"


def reject_reason(job: dict, max_years: int = 3):
    """Return a short reason string if the job is disqualified, else None."""
    title, low = job["title"], job["text"]

    if OFFTRACK_TITLE.search(title):
        return "offtrack-title"
    if not JAVA_SIGNS.search(job["text"]):
        return "no-java-evidence"
    if not ROLE_TITLE.search(title):
        return "title-not-a-dev-role"
    if is_senior_only(title):
        return "senior-only-title"
    if DEGREE_GATE.search(job["text"]):
        return "degree-gate"

    ok, why = experience_ok(job["experience"], max_years)
    if not ok:
        return why
    return None


def score_job(job: dict) -> tuple[int, list[str], list[str]]:
    """Return (score 0-100, matched skill terms, gap terms)."""
    low, title, title_low = job["text"].lower(), job["title"], job["title"].lower()
    total, matched, missing = 0, [], []

    for name, weight, terms in RUBRIC:
        hit = next((t for t in terms if t in low), None)
        if hit:
            total += weight
            matched.append(hit.strip())
        else:
            missing.append(name)

    # A Java role in the title is the single strongest signal.
    if re.search(r"\bjava\b", title_low):
        total += 12
        matched.append("java-in-title")
    elif re.search(r"\b(spring|full\s*stack|fullstack|backend|back-end)\b", title_low):
        total += 6
        matched.append("backend-title")

    if INFRA.search(low):
        total -= 10
        missing.append("infra-gap")

    # An AI/ML-titled role with no Java in the title is a different specialism
    # from this resume, so charge for it rather than letting the stack points
    # carry it over the threshold.
    if AI_TITLE.search(title_low) and not re.search(r"\bjava\b", title_low):
        total -= 14
        missing.append("ai-specialism")

    # Experience fit. A posting that states no bar at all is usually open to
    # freshers, so it counts in our favour rather than as unknown.
    band = parse_experience(job["experience"])
    if band is None:
        total += 8
        matched.append("no-bar-stated")
    else:
        lo, hi = band
        if lo <= 1:
            total += 12
            matched.append("entry-level")
        elif lo <= 2:
            total += 10
            matched.append("junior-level")
        elif lo <= 3:
            total += 6
            matched.append("up-to-3-yrs")
        elif lo <= 4:
            total += 2
        else:
            total -= 8
            missing.append("senior-experience")

    return max(0, min(100, int(total))), matched, missing


def location_bonus(job: dict, bonus_locations, bonus: int) -> int:
    """Nudge local / remote-friendly roles to the top without hard-filtering."""
    hay = " ".join([job.get("location", ""), job.get("text", "")[:800]]).lower()
    if any(k in hay for k in bonus_locations):
        return bonus
    return 0

    return 0


# ---------------------------------------------------------------------- dedup


def dedup_key(job: dict) -> str:
    """Stable identity for a job across days and across boards."""
    for u in (job.get("apply_url"), job.get("board_url")):
        if u:
            return re.sub(r"^https?://(www\.)?", "", u.lower()).rstrip("/")
    ident = (job.get("id") or "").lower()
    if ident:
        return (job.get("company", "").lower() + "::" + ident)
    return re.sub(r"\W+", "", (job.get("title", "") + job.get("company", "")).lower())[:80]


# ------------------------------------------------------------------ apify calls


def api_get(path: str):
    req = urllib.request.Request(API + path, headers=_headers())
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.load(r)["data"]


def credit_status():
    """Remaining pay-per-event budget for the current billing cycle."""
    try:
        usage = api_get("/users/me/usage/monthly")
        limits = api_get("/users/me/limits")
    except Exception:  # noqa: BLE001 - never block a run on telemetry failure
        return None
    used = sum((v or {}).get("amountAfterVolumeDiscountUsd", 0) or 0
               for v in (usage.get("monthlyServiceUsage") or {}).values())
    cap = ((limits.get("limits") or {}).get("maxMonthlyUsageUsd")
           or (usage.get("usageCycle") and 5.0) or 5.0)
    return {
        "used": used,
        "cap": cap,
        "remaining": max(0.0, cap - used),
        "cycle_start": (usage.get("usageCycle") or {}).get("startAt"),
        "cycle_end": (usage.get("usageCycle") or {}).get("endAt"),
    }


HARD_LIMIT = re.compile(r"monthly usage hard limit|platform-feature-disabled", re.I)


def run_actor(actor: str, inp: dict, wait=420) -> tuple[list, dict]:
    aid = actor.replace("/", "~")
    req = urllib.request.Request(
        f"{API}/acts/{aid}/runs?waitForFinish={wait}",
        data=json.dumps(inp).encode(),
        headers=_headers(with_json=True),
    )
    try:
        with urllib.request.urlopen(req, timeout=wait + 60) as r:
            run = json.load(r)["data"]
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", "ignore")[:300]
        return [], {"error": f"HTTP {e.code} {body}"}
    except Exception as e:  # noqa: BLE001
        return [], {"error": str(e)}

    ds = run.get("defaultDatasetId")
    items = []
    if ds:
        rq = urllib.request.Request(
            f"{API}/datasets/{ds}/items?format=json&clean=1&limit=2000",
            headers=_headers(),
        )
        try:
            with urllib.request.urlopen(rq, timeout=180) as r:
                items = json.load(r)
        except Exception:  # noqa: BLE001
            items = []
    return items, {"runId": run.get("id"), "status": run.get("status")}


def build_input(source: str, query: str, location: str, limit: int, fresh_days: int,
                max_years: int = 3):
    if source == "naukri":
        # `experience` is the actor's years-of-experience filter, so passing the
        # ceiling here stops senior postings being downloaded at all - which
        # matters because this actor bills per result returned.
        return {
            "keywords": query, "location": location, "sort": "f",
            "jobAge": str(fresh_days), "limit": limit,
            "experience": max_years, "jobPostType": ["1", "2"],
        }
    if source == "indeed":
        # Location is deliberately empty: Indeed India has no city-level
        # inventory for these roles, and passing a city returns zero results.
        # Indeed exposes no experience filter, so it is applied in post.
        return {
            "country": "in", "title": query, "location": location,
            "limit": limit, "datePosted": str(fresh_days),
        }
    if source == "glassdoor":
        # Glassdoor rejects a location shorter than 3 characters, and offers no
        # experience filter either.
        return {
            "keywords": query, "location": location or "India",
            "daysOld": fresh_days, "limit": limit,
        }
    if source == "linkedin":
        # LinkedIn only accepts pastWeek/pastMonth/anyTime (no 24h), so we
        # over-fetch and cut to fresh_days in post-processing.
        return {
            "keywords": query, "location": f"{location}, India" if location else "India",
            "datePosted": "pastWeek", "scrapeCompany": False,
            "limitPerSource": limit,
        }
    if source == "wellfound":
        slug = re.sub(r"[^a-z0-9]+", "-", query.lower()).strip("-")
        return {
            "urls": [f"https://wellfound.com/role/l/{slug}/india"],
            "pageLimit": 1, "sortBy": "LAST_POSTED", "onlyRemoteJobs": False,
        }
    raise ValueError(source)


def plan(cfg, sources):
    """Expand config into (source, query, location) triples + cost estimate."""
    work, est = [], 0.0
    fresh = cfg["freshness_days"]
    for s in sources:
        spec = cfg["sources"][s]
        limit = spec["limit"]
        queries = spec.get("queries") or cfg["queries"]
        if spec.get("usages") == "remote_only":
            for q in queries[:3]:
                work.append((s, q, spec["locations"][0], limit))
                est += START_FEE + limit * PRICE_PER_1K[s] / 1000
            continue
        # A city selection replaces whatever the config lists, so the paid
        # sweep only queries the places you ticked. Indeed has no city-level
        # filter (it returns nothing for Indian cities), so it stays pan-India
        # and is cut down locally after the download.
        locs = cfg.get("target_cities") or spec["locations"]
        if s in ("indeed", "wellfound"):
            locs = [""] if s == "indeed" else spec["locations"]
        for loc in locs:
            for q in queries:
                work.append((s, q, loc, limit))
                est += START_FEE + limit * PRICE_PER_1K[s] / 1000
    return work, est


# ------------------------------------------------------------------------- main


def write_report(ranked, cfg, stats, work, seen_count, repeats=None) -> Path:
    """Persist today's matches as JSON plus a readable markdown digest.

    `ranked` is today's new finds. `repeats` is already-shown jobs, included
    only when re-analysing the same day's data (a replay), never on a live run -
    that way a link is never handed over twice.
    """
    NL = chr(10)
    today = dt.date.today().isoformat()
    repeats = repeats or []
    # The scraped description is up to 6 KB per job and is only needed for
    # scoring, so the saved report keeps a readable excerpt instead of the wall
    # of text. This is what makes the JSON file actually inspectable.
    def lean_view(jobs):
        # `text` (6 KB) is dropped, but a 1200-char `jd` is kept: the tailored
        # resume/cover-letter generator needs the real description, not a snippet.
        return [{k: v for k, v in j.items() if k != "text"} | {
            "excerpt": re.sub(r"\s+", " ", j["text"])[:200],
            "jd": re.sub(r"\s+", " ", j["text"])[:1200]} for j in jobs]

    save_json(OUT_DIR / f"{today}.json", {
        "generatedAt": dt.datetime.now().isoformat(timespec="seconds"),
        "freshnessDays": cfg["freshness_days"], "minScore": cfg["min_score"],
        "maxExperienceYears": cfg.get("max_experience_years", 3),
        "plan": work, "stats": stats,
        "jobs": lean_view(ranked), "previouslyShown": lean_view(repeats),
    })

    lines = [f"# Fresh job matches for {today}", "",
             f"Posted within **{cfg['freshness_days']} days** | match score "
             f"**>= {cfg['min_score']}** | **{len(ranked)} new** "
             f"(dedup ledger: {seen_count})", ""]

    def table(rows):
        out = ["| # | Score | Role | Company | Location | Exp | Posted | Salary |",
               "|---|-------|------|---------|----------|-----|--------|--------|"]
        for n, j in enumerate(rows, 1):
            link = j["apply_url"] or j["board_url"]
            out.append(
                f"| {n} | **{j['score']}** | [{j['title'][:70]}]({link}) | "
                f"{j['company'][:34]} | {j['location'][:28]} | "
                f"{j['experience'][:12]} | {rel(j['posted_at'])} | "
                f"{j['salary'][:20]} |")
        return out

    def detail(rows):
        out = []
        for n, j in enumerate(rows, 1):
            out += [
                f"### {n}. {j['title']} - {j['company'] or '(company hidden)'}  "
                f"(score {j['score']}, via {j['source']})",
                f"- Apply: {j['apply_url'] or j['board_url']}",
            ]
            if j["board_url"] and j["board_url"] != j["apply_url"]:
                out.append(f"- Board: {j['board_url']}")
            out += [
                f"- Posted: {rel(j['posted_at'])} | Location: {j['location']} | "
                f"Experience: {j['experience'] or 'not stated'} | "
                f"Salary: {j['salary'] or 'not disclosed'}",
                f"- Matched: {', '.join(j['matched'])}",
                f"- Gaps: {', '.join(j['missing']) or 'none'}",
                "",
            ]
        return out

    if ranked:
        lines += table(ranked)
    else:
        lines.append("_No new jobs cleared the freshness + match threshold._")
    lines += ["", "## Detail", ""] + (detail(ranked) if ranked else [])

    if repeats:
        lines += [f"## Already shared earlier ({len(repeats)})", "",
                  "Shown to you before - included here only because this is a "
                  "re-analysis of the same day's data.", ""]
        lines += table(repeats) + ["", "## Detail (previously shared)", ""] + detail(repeats)

    path = OUT_DIR / f"{today}.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(NL.join(lines), encoding="utf-8")
    return path


def process_items(raw, source, cfg, stats) -> list:
    """Normalise, gate and score one scraper payload.

    Shared by live runs and --replay so both produce identical results.
    """
    fresh_days, min_score = cfg["freshness_days"], cfg["min_score"]
    max_years = int(cfg.get("max_experience_years", 3))
    out = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        job = normalize(item, source)
        a = job["age_days"]
        if a is None or a > fresh_days or a < -1:
            continue
        cities = cfg.get("target_cities") or []
        if cities and not hubs.matches(cities, job["location"]):
            stats.setdefault(source, {"raw": 0, "new": 0})
            stats[source]["wrong-city"] = stats[source].get("wrong-city", 0) + 1
            continue
        why = reject_reason(job, max_years)
        if why:
            stats.setdefault(source, {"raw": 0, "new": 0})
            stats[source][why] = stats[source].get(why, 0) + 1
            continue
        s, hit, miss = score_job(job)
        # A resume uploaded in the dashboard nudges the score for jobs that
        # actually mention your skills. Capped, never a blocker.
        rs = cfg.get("resume_skills") or []
        if rs:
            hay = job["text"].lower()
            job["resumeHits"] = [k for k in rs if k.lower() in hay]
            if job["resumeHits"]:
                s = min(100, s + min(5, len(job["resumeHits"])))
        s = min(100, s + location_bonus(
            job, cfg.get("location_bonus_locations", []), cfg.get("location_bonus", 0)))
        if s < min_score:
            continue
        job["key"] = dedup_key(job)
        job["score"], job["matched"], job["missing"] = s, hit, miss
        out.append(job)
    return out


def near_dup_key(job: dict) -> str:
    """Collapse one company posting the same title under several reqIds/cities."""
    t = re.sub(r"[^a-z0-9 ]+", " ", job["title"].lower())
    t = re.sub(r"\s+", " ", t).strip()
    c = re.sub(r"[^a-z0-9]+", "", job["company"].lower())
    return f"{c}::{t}"


SETTINGS_PATH = Path(__file__).resolve().parent / "state" / "settings.json"
RESUME_PATH = Path(__file__).resolve().parent / "state" / "resume.json"


def load_settings() -> dict:
    """City selection and other UI settings, written by serve.py."""
    return load_json(SETTINGS_PATH, {"cities": []})


def load_resume() -> dict:
    return load_json(RESUME_PATH, {})


def apply_settings(cfg: dict) -> dict:
    """Fold every dashboard option into the config the scraper runs with.

    The UI writes state/settings.json; this is the single place that turns
    those choices into run parameters, so what you see in the dashboard is
    exactly what the paid sweep uses.
    """
    st = load_settings()
    cities = [c for c in (st.get("cities") or []) if isinstance(c, str)]
    cfg["target_cities"] = cities

    def _int(key, name, lo, hi, default):
        v = st.get(key)
        if isinstance(v, bool) or not isinstance(v, (int, float)):
            return default
        return int(min(hi, max(lo, v)))

    cfg["min_score"] = _int("minScore", "min_score", 50, 100, cfg.get("min_score", 70))
    cfg["max_experience_years"] = _int("maxExperienceYears", "max_years", 1, 5,
                                       cfg.get("max_experience_years", 3))
    cfg["freshness_days"] = _int("freshnessDays", "freshness", 1, 30,
                                 cfg.get("freshness_days", 3))
    if isinstance(st.get("aiScreening"), bool):
        cfg["ai_screening"] = st["aiScreening"]
    if isinstance(st.get("aiQueryPlan"), bool):
        cfg["ai_query_plan"] = st["aiQueryPlan"]

    resume = load_resume()
    cfg["resume_skills"] = resume.get("skills") or []

    # Which scrapers the dashboard picker chose. An explicit pick behaves
    # like --only: it may include a source config.json leaves disabled
    # (e.g. wellfound for a day with budget to spare). Absent or emptied,
    # the run falls back to every enabled source.
    picked = [s for s in (st.get("sources") or [])
              if isinstance(s, str) and s in cfg.get("sources", {})]
    cfg["selected_sources"] = picked

    # Queries the local model rewrote for THIS form state. They replace the
    # generic config list everywhere, because each source keeps its own copy.
    qs = [q for q in (st.get("queries") or []) if isinstance(q, str) and q.strip()]
    if qs:
        cfg["queries"] = [q.strip() for q in qs]
        for spec in cfg.get("sources", {}).values():
            if spec.get("queries"):
                spec["queries"] = cfg["queries"]
    return cfg


def rewrite_queries(cfg: dict, echo=print) -> dict:
    """Ask the local model to rebuild cfg['queries'] from the dashboard form.

    Only runs when the 'AI rewrites queries' chip is on and Ollama answers;
    otherwise cfg is returned untouched, so a run always has keywords.
    """
    if not cfg.get("ai_query_plan", True):
        return cfg
    try:
        import ai_screen
    except Exception:  # noqa: BLE001
        return cfg
    if not ai_screen.available():
        echo("  AI query planning: Ollama unreachable - keeping config queries")
        return cfg
    echo("  AI query planning: asking qwen3:8b to rewrite the search keywords...")
    qs = ai_screen.plan_queries({
        "cities": cfg.get("target_cities") or [],
        "max_years": int(cfg.get("max_experience_years", 3)),
        "resume_skills": cfg.get("resume_skills") or [],
        "min_score": cfg.get("min_score", 70),
    })
    if not qs:
        echo("  AI query planning: no usable answer - keeping config queries")
        return cfg
    cfg["queries"] = qs
    for spec in cfg.get("sources", {}).values():
        if spec.get("queries"):
            spec["queries"] = qs
    echo(f"  AI query planning: {' | '.join(qs)}")
    return cfg


def ai_screen_jobs(candidates, cfg, echo=print) -> dict:
    """Second-opinion pass over jobs whose experience band was unreadable.

    The regex gate cannot judge a posting that states no band, and on these
    boards that is most of them. This asks the local Ollama model instead.
    Returns the surviving jobs. Never fatal: if the model is missing or
    unsure, every job is kept.
    """
    if not cfg.get("ai_screening"):
        return candidates
    try:
        import ai_screen
    except Exception as e:  # noqa: BLE001
        echo(f"  ai screening unavailable: {e}")
        return candidates
    if not ai_screen.available():
        echo("  ai screening skipped: Ollama not reachable on " + ai_screen.OLLAMA)
        return candidates

    need = [j for j in candidates if parse_experience(j["experience"]) is None]
    if not need:
        echo("  ai screening: every job had a readable experience band, skipped")
        return candidates

    echo(f"  ai screening {len(need)} job(s) with no stated experience "
         f"via {ai_screen.MODEL} (this takes a few minutes)...")

    def tick(done, total, job, verdict):
        if done == total or done % 3 == 0:
            mark = "fresher" if verdict.get("fresher_friendly") else "NOT fresher"
            echo(f"    [{done}/{total}] {mark:12s} {job['title'][:44]}")

    ai_screen.screen(need,
                     min_confidence=float(cfg.get("ai_min_confidence", 0.6)),
                     workers=int(cfg.get("ai_workers", 2)),
                     progress=tick)

    kept, dropped = [], 0
    for j in candidates:
        reason = ai_screen.demote(j, float(cfg.get("ai_min_confidence", 0.6)))
        if reason:
            dropped += 1
            echo(f"    dropped: {j['title'][:44]} -> {reason}")
        else:
            v = j.get("ai") or {}
            if v:
                j["ai"] = {
                    "fresher_friendly": v.get("fresher_friendly"),
                    "reason": v.get("reason"),
                    "minimum_years_required": v.get("minimum_years_required"),
                    "confidence": v.get("confidence"),
                }
                if v.get("fresher_friendly"):
                    j.setdefault("matched", []).append("ai-fresher-friendly")
            kept.append(j)
    echo(f"  ai screening kept {len(kept)}, dropped {dropped}")
    return kept


def rank_and_dedup(candidates, seen, stats):
    """Sort by score, collapse near-duplicates, split new from already-seen."""
    best = {}
    for job in sorted(candidates, key=lambda j: -j["score"]):
        best.setdefault(near_dup_key(job), job)

    now_iso = dt.datetime.now().isoformat(timespec="seconds")
    new_jobs, repeat_jobs = [], []
    for job in sorted(best.values(), key=lambda j: (-j["score"], j["age_days"] or 9e9)):
        k = job["key"]
        if k in seen:
            seen[k]["lastSeen"] = now_iso
            seen[k]["score"] = job["score"]          # refresh a stale score
            job["already_shown"] = True
            repeat_jobs.append(job)
            continue
        seen[k] = {
            "firstSeen": now_iso, "lastSeen": now_iso, "score": job["score"],
            "title": job["title"], "company": job["company"], "source": job["source"],
            "url": job["apply_url"] or job["board_url"],
        }
        new_jobs.append(job)
        stats.setdefault(job["source"], {"raw": 0, "new": 0})["new"] += 1
    return new_jobs, repeat_jobs


def print_results(jobs, cfg, label="MATCHES"):
    """Readable console output - a compact scannable list, not a JSON blob."""
    print()
    for j in jobs:
        link = j["apply_url"] or j["board_url"]
        print(f"  [{j['score']:3d}] {j['title'][:58]}")
        print(f"         {j['company'] or '(company hidden)'} - {j['location'] or 'India'} "
              f"| {j['experience'] or 'exp n/s'} | {j['salary'] or 'salary n/d'} "
              f"| {rel(j['posted_at'])} | {j['source']}")
        print(f"         {link}")
        print(f"         match: {', '.join(j['matched']) or '-'}"
              + (f"   gap: {', '.join(j['missing'])}" if j["missing"] else ""))
        print()


def replay(cfg) -> int:
    """Re-score the raw payloads already on disk. Costs nothing."""
    files = sorted(RAW_DIR.glob("*.json"))
    if not files:
        print(f"No saved payloads in {RAW_DIR}. Run `python hunt.py` once first.")
        return 1

    stats, candidates = {}, []
    for f in files:
        source = f.stem.split("_")[0]
        try:
            raw = load_json(f, [])
        except Exception as e:  # noqa: BLE001
            print(f"  skip {f.name}: {e}")
            continue
        jobs = process_items(raw, source, cfg, stats)
        candidates.extend(jobs)
        print(f"  {f.name:26s} {len(raw):4d} raw -> {len(jobs):3d} above "
              f"{cfg['min_score']}")

    candidates = ai_screen_jobs(candidates, cfg)

    seen = load_json(STATE_PATH, {})
    new_jobs, repeat_jobs = rank_and_dedup(candidates, seen, stats)
    save_json(STATE_PATH, seen)
    report = write_report(new_jobs, cfg, stats, [], len(seen), repeats=repeat_jobs)

    print("\n" + "=" * 70)
    print(f"replayed {len(files)} saved payloads | {len(new_jobs)} new, "
          f"{len(repeat_jobs)} already in the ledger")
    print("filtered out: " + ", ".join(
        f"{s}:{v[k]} {k}" for s, v in sorted(stats.items())
        for k in v if k not in ("raw", "new", "errors")))
    print(f"report -> {report}")
    if new_jobs:
        print(f"\n{'-'*70}\nNEW MATCHES ({len(new_jobs)})\n{'-'*70}")
        print_results(new_jobs, cfg)
    if repeat_jobs:
        print(f"{'-'*70}\nALREADY SHOWN BEFORE ({len(repeat_jobs)})\n{'-'*70}")
        print_results(repeat_jobs, cfg)
    return 0


def main():
    global TOKEN
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true",
                    help="print the run plan and cost, run nothing")
    ap.add_argument("--replay", action="store_true",
                    help="re-score saved raw payloads in out/raw, spending no credit")
    ap.add_argument("--show-seen", action="store_true")
    ap.add_argument("--reset", action="store_true")
    ap.add_argument("--only", default="", help="comma-separated sources")
    ap.add_argument("--min-score", type=int, default=None)
    ap.add_argument("--max-years", type=int, default=None,
                    help="fresher ceiling: keep jobs needing <= this many years")
    ap.add_argument("--no-ai", action="store_true",
                    help="skip the local Ollama second-opinion pass")
    ap.add_argument("--ai-only", action="store_true",
                    help="run the AI pass and exit (uses saved raw data)")
    args = ap.parse_args()

    if args.show_seen:
        st = load_json(STATE_PATH, {})
        print(f"{len(st)} jobs in ledger ({STATE_PATH})")
        for k, v in list(st.items())[:20]:
            print("  ", v.get("firstSeen", "?")[:10], k[:70])
        return
    if args.reset:
        save_json(STATE_PATH, {})
        print("ledger cleared")
        return

    cfg = apply_settings(load_json(CONFIG_PATH, {}))
    cities = cfg.get("target_cities") or []
    if cities:
        print(f"Location filter: {', '.join(cities)} (+ remote)")
    if cfg.get("resume_skills"):
        print(f"Resume loaded: {len(cfg['resume_skills'])} skills will "
              f"boost matching jobs by up to 5 points.")
    cfg = rewrite_queries(cfg)          # Ollama owns the keywords from here
    st = load_settings()
    st["queries"] = cfg.get("queries") or []
    save_json(SETTINGS_PATH, st)        # so the dashboard shows what was asked
    if args.min_score:
        cfg["min_score"] = args.min_score
    if args.max_years is not None:
        cfg["max_experience_years"] = args.max_years
    if args.no_ai:
        cfg["ai_screening"] = False

    if args.replay:
        return replay(cfg)

    TOKEN = api_token()
    if args.only:
        # An explicit --only is a deliberate override, so it may select a
        # source that config.json has disabled (e.g. a pricier one for a day).
        sources = [s.strip() for s in args.only.split(",") if s.strip() in cfg["sources"]]
    else:
        sources = cfg.get("selected_sources") or \
            [s for s in cfg["sources"] if cfg["sources"][s].get("enabled")]

    work, est = plan(cfg, sources)
    print(f"{len(work)} scraper runs planned across {len(sources)} sources.")
    print(f"Experience ceiling: <= {cfg.get('max_experience_years', 3)} yrs "
          f"(pushed into Naukri, applied locally elsewhere).")
    print(f"Estimated max cost: ${est:.3f}  (pay-per-event, billed on results only)")

    credit = credit_status()
    if credit:
        print(f"Budget cycle {str(credit['cycle_start'])[:10]} -> {str(credit['cycle_end'])[:10]}: "
              f"${credit['used']:.2f} of ${credit['cap']:.2f} used, "
              f"${credit['remaining']:.2f} left.")
        if est > credit["remaining"]:
            print(f"\nREFUSING: this plan needs ~${est:.3f} but only "
                  f"${credit['remaining']:.2f} remains. Trim config.json "
                  f"(fewer queries/locations) or raise the Apify plan.")
            print("No scraper runs were started.")
            return 2
        if est > credit["remaining"] * 0.6:
            print(f"WARNING: this plan uses {est / credit['remaining']:.0%} of the "
                  f"remaining cycle budget.")

    if args.dry_run:
        for s, q, loc, lim in work:
            print(f"  {s:10s} {q:28s} {loc:12s} limit={lim}")
        return

    seen = load_json(STATE_PATH, {})
    fresh_days, min_score = cfg["freshness_days"], cfg["min_score"]
    stats = {}

    max_years = int(cfg.get("max_experience_years", 3))

    def harvest(idx, source, query, loc, limit):
        """Worker: fetch + normalise + filter. Pure, safe to run in a thread."""
        label = f"{source}: {query} @ {loc}"
        try:
            raw, meta = run_actor(
                cfg["sources"][source]["actor"],
                build_input(source, query, loc, limit, fresh_days, max_years),
            )
        except Exception as e:  # noqa: BLE001
            return label, [], {"error": str(e)}
        if meta.get("error"):
            return label, [], meta

        RAW_DIR.mkdir(parents=True, exist_ok=True)
        with open(RAW_DIR / f"{source}_{idx:03d}.json", "w", encoding="utf-8") as f:
            json.dump(raw, f, indent=1, ensure_ascii=False)

        out = process_items(raw, source, cfg, stats)
        return label, out, {"raw": len(raw)}

    workers = int(cfg.get("concurrency", 5))
    candidates = []
    aborted = False
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = [pool.submit(harvest, i, s, q, l, lim)
                   for i, (s, q, l, lim) in enumerate(work, 1)]
        for i, fut in enumerate(as_completed(futures), 1):
            label, jobs, meta = fut.result()
            st = stats.setdefault(label.split(":")[0], {"raw": 0, "new": 0})
            if meta.get("error"):
                st["errors"] = st.get("errors", 0) + 1
                if HARD_LIMIT.search(str(meta["error"])) and not aborted:
                    aborted = True
                    print(f"\n*** Apify monthly usage hard limit reached. "
                          f"Aborting sweep - no more runs will be started. ***")
                    for f in futures:
                        f.cancel()
                    break
                print(f"[{i}/{len(work)}] {label} ERROR {meta['error'][:110]}", flush=True)
                continue
            st["raw"] += meta.get("raw", 0)
            candidates.extend(jobs)
            print(f"[{i}/{len(work)}] {label} -> {meta.get('raw', 0)} raw, "
                  f"{len(jobs)} above {min_score}", flush=True)

    candidates = ai_screen_jobs(candidates, cfg)

    # Collapse near-duplicates, then split new from already-seen against the
    # persistent ledger.
    matched, repeat_jobs = rank_and_dedup(candidates, seen, stats)

    ranked = matched
    save_json(STATE_PATH, seen)
    report = write_report(ranked, cfg, stats, work, len(seen))
    print("\n" + "=" * 70)
    for s, v in stats.items():
        err = f"  errors={v['errors']}" if v.get("errors") else ""
        print(f"  {s:10s} raw={v['raw']:4d}  new={v['new']:3d}{err}")
    print(f"\n{len(ranked)} new matches -> {report}")
    if ranked:
        print_results(ranked, cfg)
    return 0


if __name__ == "__main__":
    raise SystemExit(main() or 0)
