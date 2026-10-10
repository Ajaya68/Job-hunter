# Job Hunter

Finds new, recently-posted jobs that match your resume, across LinkedIn, Naukri,
Indeed, Wellfound and Glassdoor. Anything it has already shown you is never shown
again, on any later day.

## Daily use

```bash
cd job-hunter
python hunt.py
```

Results land in `out/YYYY-MM-DD.md` (readable digest) and `out/YYYY-MM-DD.json`.

### Dashboard (recommended)

```bash
python serve.py
```

Opens http://127.0.0.1:8787 - browse every match as a card, and press **Run
scrape** to fetch new jobs without leaving the page. The button first shows what
the run will cost and waits for you to confirm, then streams the scraper log live
and refreshes the cards when it finishes.

Everything ends up in one self-contained file: `python dashboard.py` rebuilds
`out/dashboard.html`, which you can also open straight from disk. The Run button
is disabled in that case, because a page opened as a file cannot start a backend.

In the dashboard you can search, filter by source, raise the minimum score, show
only fresher-friendly roles (no experience bar, or 0-3 yrs), sort by match or
date, mark jobs applied (remembered per browser), and copy a link.

Three buttons sit beside **Run scrape**:

| Button | What it does |
|---|---|
| **Location** | Search and tick any of 30 Indian IT hubs (Bengaluru, Hyderabad, Pune, Bhubaneswar, Berhampur, ...). The next scrape queries only those cities and the cards on screen are filtered to them; remote/WFH roles always stay. An "All India" checkbox ignores the list. Saved in `state/settings.json`. |
| **Resume** | Upload your actual resume file - **PDF, Word (.docx), OpenDocument, RTF, LaTeX (.tex), HTML, Markdown or plain text** - or paste text. The file is decoded on this machine (stdlib only, no uploads), known skills are extracted, and every job mentioning them gains up to **+5 points**, so equal-looking roles rank by how well they fit you. Saved in `state/resume.json`. Image-only/scanned PDFs cannot be read: the error tells you to paste the text instead. |
| **Delete data** | Remove stored data section by section - raw payloads (optionally only older than N days), the seen-jobs ledger, or daily reports. Every path is re-checked server-side and unknown scopes are refused. |

The **AI** pill in the header shows whether the local Ollama model is reachable.
On a run, `qwen3:8b` reads every job whose experience band the scraper could not
read and only drops it when confidently not fresher-friendly. If Ollama is down
the run continues with regex-only screening (never fatal).

### API (used by the dashboard)

| Endpoint | Purpose |
|---|---|
| `GET/POST /api/settings` | Read/save the selected cities |
| `GET/POST /api/resume` | Read/save resume text, returns extracted skills |
| `POST /api/resume/file` | Upload a resume file (base64 PDF/docx/tex/...), returns extracted skills |
| `POST /api/resume/delete` | Forget the uploaded resume |
| `GET /api/ai` | Ollama reachability + model name |
| `POST /api/data/delete` | Delete a data section (`raw`, `ledger`, `reports`) |

Other commands:

| Command | Purpose |
|---|---|
| `python hunt.py --dry-run` | Show the run plan and cost, start nothing |
| `python hunt.py --replay` | Re-score the last run from saved data, costs nothing |
| `python dashboard.py --open` | Rebuild the HTML and open it |
| `python prune.py` | Show what a cleanup would remove (dry run) |
| `python prune.py --prune` | Delete raw payloads older than 14 days and stale ledger entries |
| `python hunt.py --show-seen` | Inspect the dedup ledger |
| `python hunt.py --reset` | Wipe the ledger so everything is re-surfaced |
| `python hunt.py --only naukri,indeed` | Run a subset of sources |
| `python hunt.py --min-score 80` | Override the match threshold |
| `python test_scoring.py` | Verify the match logic (offline, free) |
| `python test_pipeline.py` | Verify dedup + report output (offline, free) |

## How a job gets to you

1. **Freshness** — posted within `freshness_days` (3). Each source pushes this
   into the scraper *and* it is re-checked locally, because LinkedIn cannot filter
   to 72 hours (only past 24h / past week / past month).
2. **Hard gates** — rejected outright if the role is off-track (.NET, PHP, SAP,
   React Native, QA, data science, non-engineering), has no Java evidence,
   is senior-only, needs 5+ years, or demands an MTech/PhD.
3. **Score** — survivors are scored 0-100 across role type (30), Java stack (22),
   database (12), API (10), frontend (8), tooling (6), domain (6), plus
   +12 for Java in the title and +10 for a 0-3 year band. A local or remote
   location adds +6. Only scores **>= 70** are reported.
4. **Dedup** — three layers:
   - a stable key from the direct/apply URL (tracking params stripped), else the
     job id, else title+company;
   - near-duplicate collapse, so one company posting the same title under four
     reqIds counts once;
   - the persistent ledger `state/seen.json`, so a link given to you yesterday
     is never repeated today.

## Cost

Every source is pay-per-event, so you are billed per result, not per run. Current
per-1000-results rates: Indeed $0.07, Naukri $0.28, Glassdoor $0.28,
LinkedIn $1.00, Wellfound $2.79.

The default plan is **27 runs, ~$0.15/day (~$4.50/month)**. That fits the Apify
FREE plan's $5 monthly credit. `hunt.py` checks your remaining credit before
starting and refuses to run if the plan would not fit, so a run can never burn
the balance and then fail half way.

To spend less, cut `queries` or `locations` per source in `config.json`, or drop
`limit`. To spend more on a given day, enable a pricier source:

```bash
python hunt.py --only wellfound,linkedin
```

## Editing

- `config.json` — queries, per-source locations, limits, thresholds, concurrency.
- `profile/extract.py` — re-derives `profile/resume_profile.json` from
  `../resume.tex`. Run it after you update the resume, then re-tune the rubric in
  `hunt.py` (`RUBRIC`, `JAVA_SIGNS`, `OFFTRACK_TITLE`, `SENIOR_ONLY`).

If you change the scoring rules, update the cases in `test_scoring.py` and run it
first — it is offline and free.
