"""Build a self-contained HTML dashboard from the latest hunt report.

No server and no third-party libraries: the data is embedded in the file, so it
opens straight from disk and keeps working offline.

    python dashboard.py            # rebuild from the most recent report
    python dashboard.py --open     # ...and open it in the default browser
"""
from __future__ import annotations

import argparse
import html
import json
import webbrowser
from pathlib import Path

import hubs  # noqa: E402  # shared India IT-hub list

HERE = Path(__file__).resolve().parent
OUT_DIR = HERE / "out"

TEMPLATE = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Job Hunt Dashboard</title>
<style>
:root{
  --bg:#0f1419; --panel:#171e26; --panel2:#1e2731; --line:#2a3542;
  --fg:#e8eef5; --dim:#93a3b5; --accent:#4da3ff;
  --green:#3fb950; --amber:#d29922; --red:#f85149; --violet:#a371f7;
}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--fg);
  font:15px/1.5 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,Helvetica,Arial,sans-serif}
a{color:var(--accent);text-decoration:none}
a:hover{text-decoration:underline}
header{padding:20px 24px 14px;border-bottom:1px solid var(--line);background:var(--panel);
  position:sticky;top:0;z-index:20}
h1{margin:0;font-size:20px;letter-spacing:.2px}
h1 span{color:var(--dim);font-weight:400;font-size:14px;margin-left:10px}
.stats{display:flex;flex-wrap:wrap;gap:8px;margin-top:12px}
.stat{background:var(--panel2);border:1px solid var(--line);border-radius:8px;
  padding:7px 12px;font-size:13px}
.stat b{display:block;font-size:17px;line-height:1.2}
/* ---- right-hand filter panel: every knob that shapes the paid run ---- */
.layout{display:flex;align-items:flex-start}
.content{flex:1;min-width:0;padding:20px 24px 60px}
#side{width:330px;flex:none;background:var(--panel);border-left:1px solid var(--line);
  position:sticky;top:var(--hdr,0px);height:calc(100vh - var(--hdr,0px));
  overflow:auto;padding:14px 14px 50px}
#side h3{font-size:12px;text-transform:uppercase;letter-spacing:.7px;
  color:var(--dim);margin:16px 0 9px}
#side h3:first-child{margin-top:0}
.fsec{border-bottom:1px solid var(--line);padding-bottom:14px}
.fsec:last-of-type{border-bottom:none}
/* the scraper runs on every filter above it, so it pins to the panel bottom */
.fsec.run{position:sticky;bottom:0;background:var(--panel);padding-top:14px;
  border-top:1px solid var(--line)}
.fsec label.ctl{display:block;color:var(--dim);font-size:13px;margin:8px 0 4px}
.fsec select,.fsec input[type=search]{width:100%;box-sizing:border-box}
.fsec .hint{color:var(--dim);font-size:12px;margin:7px 0 0;line-height:1.45}
.pf{display:block;color:var(--dim);font-size:12.5px;margin-top:10px;font-weight:600}
.pf input,.pf textarea{display:block;width:100%;box-sizing:border-box;margin-top:4px;
  background:var(--bg);color:var(--fg);border:1px solid var(--line);border-radius:8px;
  padding:8px 10px;font:inherit;font-weight:400;resize:vertical}
.pf input:focus,.pf textarea:focus{outline:none;border-color:var(--accent)}
.fsec .chips{display:flex;gap:6px;flex-wrap:wrap;margin-top:6px}
#side .row{margin-bottom:0}
#side input[type=range]{width:calc(100% - 42px)}
#side .btn{width:100%;box-sizing:border-box;padding:9px 12px;font-size:14px}
#side .btn.auto{width:auto}
#runBtn{background:var(--green);border-color:var(--green);color:#04120a;font-weight:700}
#runBtn:disabled{opacity:.45;cursor:not-allowed}
#qList{margin-top:8px;font-size:12.5px;color:var(--dim);line-height:1.7}
#qList .q{display:inline-block;background:var(--panel2);border:1px solid var(--line);
  border-radius:6px;padding:2px 8px;margin:0 4px 4px 0;color:var(--fg)}
#qList .q.gone{opacity:.45;text-decoration:line-through}
.row{display:flex;flex-wrap:wrap;gap:10px;align-items:center;margin-bottom:10px}
input[type=search],select{background:var(--panel2);color:var(--fg);border:1px solid var(--line);
  border-radius:7px;padding:8px 11px;font-size:14px}
input[type=search]{flex:1;min-width:210px}
input[type=range]{accent-color:var(--accent);vertical-align:middle}
.chip{background:var(--panel2);border:1px solid var(--line);color:var(--dim);
  border-radius:999px;padding:6px 13px;font-size:13px;cursor:pointer;user-select:none}
.chip.on{background:var(--accent);border-color:var(--accent);color:#08121c;font-weight:600}
.chip.filter.on{background:var(--green);border-color:var(--green)}
.tabs{display:flex;gap:6px;border-bottom:1px solid var(--line);padding:0 24px;
  background:var(--panel);position:sticky;top:0;z-index:18}
.tab{padding:12px 16px;cursor:pointer;color:var(--dim);border-bottom:2px solid transparent;
  font-size:14px}
.tab.on{color:var(--fg);border-bottom-color:var(--accent);font-weight:600}
main{padding:20px 24px 60px}
.grid{display:grid;gap:14px;grid-template-columns:repeat(auto-fill,minmax(390px,1fr))}
.card{background:var(--panel);border:1px solid var(--line);border-radius:10px;
  padding:15px 16px;display:flex;flex-direction:column;gap:9px}
.card.applied{opacity:.5}
.card.applied .ttl{text-decoration:line-through}
.top{display:flex;justify-content:space-between;align-items:flex-start;gap:12px}
.ttl{font-size:16px;font-weight:600;line-height:1.35}
.co{color:var(--dim);font-size:13.5px;margin-top:3px}
.score{flex:none;font-weight:700;font-size:15px;padding:5px 10px;border-radius:7px;
  background:var(--panel2);border:1px solid var(--line)}
.s-hi{color:var(--green);border-color:var(--green)}
.s-mid{color:var(--accent);border-color:var(--accent)}
.s-lo{color:var(--amber);border-color:var(--amber)}
.meta{display:flex;flex-wrap:wrap;gap:6px}
.m{font-size:12.5px;color:var(--dim);background:var(--panel2);border:1px solid var(--line);
  border-radius:6px;padding:3px 8px}
.m.fresh{color:var(--green);border-color:var(--green)}
.m.local{color:var(--violet);border-color:var(--violet)}
.m.jr{color:var(--green);border-color:rgba(63,185,80,.5)}
.tags{display:flex;flex-wrap:wrap;gap:5px}
.t{font-size:11.5px;padding:2px 7px;border-radius:5px;border:1px solid transparent}
.t.ok{color:var(--green);background:rgba(63,185,80,.1);border-color:rgba(63,185,80,.35)}
.t.gap{color:var(--amber);background:rgba(210,153,34,.1);border-color:rgba(210,153,34,.35)}
.acts{display:flex;gap:8px;margin-top:auto;padding-top:9px;flex-wrap:wrap}
.btn{font-size:13px;padding:7px 13px;border-radius:7px;border:1px solid var(--line);
  background:var(--panel2);color:var(--fg);cursor:pointer}
.btn.p{background:var(--accent);border-color:var(--accent);color:#08121c;font-weight:600;
  text-decoration:none;display:inline-block}
.btn.p:hover{text-decoration:none;filter:brightness(1.1)}
.btn:hover{border-color:var(--accent)}
.empty{color:var(--dim);text-align:center;padding:60px 20px}
.note{margin:26px 0 0;padding:14px 16px;background:var(--panel);border:1px solid var(--line);
  border-radius:10px;color:var(--dim);font-size:13.5px}
.note b{color:var(--fg)}
details{margin-top:8px}
summary{cursor:pointer;color:var(--dim);font-size:13px}
.kv{display:grid;grid-template-columns:auto 1fr;gap:3px 14px;margin-top:9px;font-size:13px}
.kv dt{color:var(--dim)}
.count{color:var(--dim);font-size:13px;margin:0 0 14px}
/* run controls */
#runBtn{background:var(--green);border-color:var(--green);color:#04120a;font-weight:700}
#runBtn:disabled{opacity:.45;cursor:not-allowed}
.hdr-row{display:flex;justify-content:space-between;align-items:flex-start;gap:16px;
  flex-wrap:wrap}
#confirm{display:none;margin-top:14px;padding:13px 15px;background:var(--panel2);
  border:1px solid var(--amber);border-radius:9px;align-items:center;gap:12px;flex-wrap:wrap}
#confirm.show{display:flex}
#confirm .txt{font-size:13.5px;flex:1;min-width:230px}
#confirm b{color:var(--amber)}
#console{display:none;margin:0 0 18px;background:#05080b;border:1px solid var(--line);
  border-radius:9px;padding:12px 14px;max-height:230px;overflow:auto;
  font:12.5px/1.55 ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;color:#b9c6d4}
#console.show{display:block}
#console .e{color:var(--red)} #console .w{color:var(--amber)} #console .g{color:var(--green)}
.dot{display:inline-block;width:8px;height:8px;border-radius:50%;background:var(--dim);
  margin-right:7px;vertical-align:middle}
.dot.on{background:var(--green);animation:pulse 1.1s infinite}
@keyframes pulse{0%,100%{opacity:1}50%{opacity:.25}}
.pill{font-size:12px;color:var(--dim);border:1px solid var(--line);border-radius:999px;
  padding:4px 11px;background:var(--panel2)}
.pill.bad{color:var(--red);border-color:var(--red)}
/* data manager */
.hublist{display:grid;grid-template-columns:repeat(auto-fill,minmax(170px,1fr));
  gap:6px;max-height:44vh;overflow:auto;padding:4px 0}
.hublist label{font-size:13.5px;padding:5px 8px;border:1px solid var(--line);
  border-radius:7px;cursor:pointer;display:flex;gap:7px;align-items:center}
.hublist label:hover{border-color:var(--dim)}
.hublist label:has(input:checked){border-color:var(--accent);background:#10261f;color:var(--fg)}
.hublist .none{color:var(--dim);font-size:13px;padding:8px 0}
#locSearch,#resumeText,#resumeFile{width:100%;background:var(--bg);color:var(--fg);
  border:1px solid var(--line);border-radius:8px;padding:8px 10px;font-size:14px;
  font-family:inherit}
#resumeText{margin-top:8px;resize:vertical}
#locBtn.on,#resumeBtn.on{border-color:var(--accent);color:var(--fg)}
.tailorout{background:var(--bg);border:1px solid var(--line);border-radius:9px;
  padding:14px 16px;max-height:56vh;overflow:auto;font-size:13.5px;
  line-height:1.55;white-space:pre-wrap;word-break:break-word}
.tailorout h1,.tailorout h2,.tailorout h3{font-size:15px;margin:12px 0 6px;
  color:var(--fg)}
.tailorout ul{margin:6px 0 6px 18px;padding:0}
.tailorout li{margin:3px 0}
.tailorout .spin{color:var(--dim)}
#aiChip.on{border-color:var(--violet);color:var(--fg)}
#dataBtn{color:var(--red);border-color:rgba(248,81,73,.5)}
#dataBtn:hover{background:var(--red);border-color:var(--red);color:#fff}
dialog{background:var(--panel);color:var(--fg);border:1px solid var(--line);
  border-radius:12px;padding:0;max-width:560px;width:94vw;
  box-shadow:0 18px 50px rgba(0,0,0,.6)}
dialog::backdrop{background:rgba(0,0,0,.6)}
.dlg-h{padding:16px 20px;border-bottom:1px solid var(--line);display:flex;
  justify-content:space-between;align-items:center}
.dlg-h h2{margin:0;font-size:16px}
.dlg-b{padding:18px 20px;max-height:64vh;overflow:auto}
.dlg-f{padding:14px 20px;border-top:1px solid var(--line);display:flex;
  justify-content:flex-end;gap:9px;background:var(--panel2)}
.sec{border:1px solid var(--line);border-radius:9px;padding:13px 14px;margin-bottom:11px;
  background:var(--panel2)}
.sec .row{display:flex;justify-content:space-between;align-items:center;gap:12px;
  margin:0}
.sec .sub{color:var(--dim);font-size:12.5px;margin:6px 0 0}
.sec select,.sec input{background:var(--panel);color:var(--fg);border:1px solid var(--line);
  border-radius:6px;padding:6px 9px;font-size:13px;margin-top:8px}
.danger{border-color:var(--red)!important;color:var(--red)!important}
.danger:hover{background:var(--red)!important;color:#fff!important}
#toast{position:fixed;bottom:20px;left:50%;transform:translateX(-50%);
  background:var(--panel2);border:1px solid var(--green);color:var(--fg);
  padding:11px 20px;border-radius:9px;font-size:13.5px;z-index:99;display:none;
  box-shadow:0 8px 26px rgba(0,0,0,.5)}
#toast.show{display:block}
#toast.err{border-color:var(--red)}
</style>
</head>
<body>
<header>
  <div class="hdr-row">
    <h1>Java Job Hunt <span id="when"></span></h1>
    <div style="display:flex;gap:8px;align-items:center;flex-wrap:wrap">
      <span class="pill" id="aiPill" title="Local Ollama model used as the second brain">AI: ...</span>
      <span class="pill" id="statusPill"><span class="dot" id="dot"></span><span id="statusTxt">idle</span></span>
      <button class="btn" id="resumeBtn" title="Upload a resume - Ollama splits it into editable sections">Resume</button>
      <button class="btn danger" id="dataBtn" title="Delete stored data section by section">Delete data</button>
    </div>
  </div>
  <div class="stats" id="stats"></div>
</header>

<div class="layout">
<div class="content">
<div class="tabs">
  <div class="tab on" data-tab="new">New today</div>
  <div class="tab" data-tab="old">Already shared earlier</div>
  <div class="tab" data-tab="all">Everything</div>
</div>

<main>
  <div id="confirm">
    <div class="txt" id="confirmTxt"></div>
    <button class="btn p" id="goBtn">Run now</button>
    <button class="btn" id="cancelBtn">Cancel</button>
  </div>
  <div id="console"></div>
  <p class="count" id="count"></p>
  <div class="grid" id="grid"></div>
  <div class="empty" id="empty" style="display:none">Nothing matches these filters.</div>
  <div class="note" id="why"></div>
</main>
</div>

<!-- ===== right-side form: every control here shapes the paid Apify run ===== -->
<form id="filters" autocomplete="off">
<aside id="side">
  <div class="fsec">
    <h3>Experience</h3>
    <label class="ctl" for="my">Max experience asked</label>
    <select id="my">
      <option value="1">Fresher (0-1 yr)</option>
      <option value="2">0-2 yrs</option>
      <option value="3" selected>0-3 yrs</option>
      <option value="4">0-4 yrs</option>
      <option value="5">any</option>
    </select>
    <div class="chips">
      <div class="chip filter" id="jr" role="button" tabindex="0">Fresher-friendly only</div>
    </div>
    <p class="hint">Fresher pushes <code>experience=1</code> into the Naukri
      actor itself, so senior posts are never downloaded (you pay for results).</p>
  </div>

  <div class="fsec">
    <h3>Freshness</h3>
    <label class="ctl" for="fresh">Posted within</label>
    <select id="fresh">
      <option value="1">1 day</option>
      <option value="3" selected>3 days</option>
      <option value="7">7 days</option>
      <option value="14">14 days</option>
    </select>
  </div>

  <div class="fsec">
    <h3>Match score</h3>
    <label class="ctl" for="ms">Min score: <b id="msv" style="color:var(--fg)">50</b></label>
    <input type="range" id="ms" min="50" max="100" step="5" value="50">
  </div>

  <div class="fsec">
    <h3>Cities</h3>
    <input type="search" id="locSearch" placeholder="Search an IT hub...">
    <label class="ctl" style="margin-top:8px">
      <input type="checkbox" id="locAll"> All India (ignore the list)</label>
    <div id="locList" class="hublist"></div>
    <div class="chips" style="margin-top:8px">
      <button type="button" class="btn auto" id="locClear">Clear</button>
      <button type="button" class="btn p auto" id="locSave">Save cities</button>
    </div>
    <p class="hint" id="locHint"></p>
  </div>

  <div class="fsec">
    <h3>Job scrapers</h3>
    <div class="chips">
      <div class="chip" id="srcAll">All scrapers</div>
      <span id="srcs" style="display:flex;gap:6px;flex-wrap:wrap"></span>
    </div>
    <p class="hint" id="srcHint">Tick the boards the next paid run may scrape.
      Unticking all but one runs a single-scraper hunt.</p>
  </div>

  <div class="fsec">
    <h3>Search</h3>
    <input type="search" id="q" placeholder="Filter the cards below...">
    <label class="ctl" for="sort">Sort by</label>
    <select id="sort">
      <option value="score">best match</option>
      <option value="new">newest first</option>
      <option value="old">oldest first</option>
      <option value="company">company A-Z</option>
    </select>
  </div>

  <div class="fsec">
    <h3>AI screening</h3>
    <div class="chips">
      <div class="chip filter on" id="aiChip" role="button" tabindex="0"
        title="Local Ollama second opinion on jobs with no stated experience">AI screening</div>
      <div class="chip filter on" id="aiPlanChip" role="button" tabindex="0"
        title="Before the run, ask Ollama to rewrite the search keywords">AI rewrites queries</div>
    </div>
    <div id="qList"></div>
    <p class="hint">With both on: Ollama rewrites the Apify search keywords
      from your filters + resume, then screens the unclear postings. Nothing
      leaves this machine.</p>
  </div>

  <div class="fsec">
    <h3>Resume</h3>
    <button type="button" class="btn" id="resumeOpen">Upload / edit resume</button>
    <p class="hint">Upload your resume once - Ollama reads it and tunes the
      scraper (search phrases, cities) to you. Skills in it add up to +5
      points. Then edit any section by hand.</p>
  </div>

  <div class="fsec run">
    <h3>Run the scraper</h3>
    <button type="button" class="btn" id="runBtn">Run scrape</button>
    <button type="button" class="btn danger" id="stopBtn" style="display:none">Stop</button>
    <p class="hint">Every filter above is saved first, then sent to the Apify
      actors. <b>Ollama (qwen3:8b) is the brain</b>: it rewrites the search
      phrases from your resume + filters, then screens what the regex cannot
      read, before results reach this screen.</p>
  </div>
</aside>
</form>
</div>

<dialog id="resumeDlg">
  <div class="dlg-h">
    <h2>Your resume</h2>
    <button class="btn" id="resumeClose">Close</button>
  </div>
  <div class="dlg-b">
    <p style="color:var(--dim);font-size:13px">Upload a file or paste text -
      <b>Ollama reads it, fills every section below, and tunes the job
      scraper to you</b> (search phrases + cities). Fix anything it got wrong
      by hand and press Save. Nothing leaves this machine.</p>
    <input type="file" id="resumeFile"
      accept=".pdf,.docx,.doc,.pptx,.odt,.rtf,.tex,.latex,.md,.txt,.html,.htm,.json,.csv">
    <div id="fileHint" style="color:var(--dim);font-size:12.5px;margin-top:5px">
      PDF, Word (.docx), OpenDocument, RTF, LaTeX (.tex), HTML, Markdown or plain text.</div>
    <textarea id="resumeText" rows="5" placeholder="...or paste resume text here"></textarea>
    <div id="resumeOut" style="margin-top:8px;font-size:13px"></div>

    <div id="autoNote" style="display:none;margin-top:12px;padding:9px 12px;
      border:1px solid var(--violet);border-radius:8px;font-size:13px;
      color:var(--dim)"></div>

    <form id="profForm" autocomplete="off">
    <div class="pgrid" style="display:grid;grid-template-columns:1fr 1fr;gap:10px;margin-top:14px">
      <label class="pf">Name<input type="text" id="pfName" placeholder="Ajaya Mahanty"></label>
      <label class="pf">Phone<input type="text" id="pfPhone" placeholder="+91 ..."></label>
      <label class="pf">Email<input type="email" id="pfEmail" placeholder="you@example.com"></label>
      <label class="pf">Location<input type="text" id="pfLoc" placeholder="Berhampur, Odisha"></label>
    </div>
    <label class="pf">Summary / objective
      <textarea id="pfSummary" rows="3" placeholder="3 lines about you"></textarea></label>
    <label class="pf">Skills <span style="font-weight:400">(comma separated - edit freely)</span>
      <textarea id="pfSkills" rows="2" placeholder="Java, Spring Boot, React..."></textarea></label>
    <label class="pf">Education <span style="font-weight:400">(one line each)</span>
      <textarea id="pfEdu" rows="3" placeholder="BCA - University, 2026 - CGPA 8.5"></textarea></label>
    <label class="pf">Projects <span style="font-weight:400">(one line each)</span>
      <textarea id="pfProj" rows="4" placeholder="Blog API - Java, Spring Boot, Oracle: ..."></textarea></label>
    <label class="pf">Certifications
      <textarea id="pfCert" rows="2" placeholder="Naresh IT Java Full Stack..."></textarea></label>
    <label class="pf">Strengths / languages / achievements
      <textarea id="pfStrength" rows="2" placeholder="Fast learner, English, Hindi, Odia"></textarea></label>
    </form>
    <div id="resumeOut2" style="margin-top:8px;font-size:13px"></div>
  </div>
  <div class="dlg-f">
    <span id="resumeHint" style="flex:1;color:var(--dim);font-size:12.5px"></span>
    <button class="btn" id="resumeDel" style="display:none;color:var(--red)">Remove</button>
    <button class="btn p" id="resumeSave">Save resume</button>
  </div>
</dialog>

<dialog id="tailorDlg">
  <div class="dlg-h">
    <h2 id="tailorTitle">Tailored application</h2>
    <button class="btn" id="tailorClose">Close</button>
  </div>
  <div class="dlg-b">
    <div class="tabs" style="margin-bottom:10px">
      <div class="tab on" data-ttab="resume">Tailored resume</div>
      <div class="tab" data-ttab="cover">Cover letter</div>
    </div>
    <div id="tailorOut" class="tailorout"><span style="color:var(--dim)">Pick a tab to generate it with the local AI model.</span></div>
  </div>
  <div class="dlg-f">
    <span id="tailorHint" style="flex:1;color:var(--dim);font-size:12.5px"></span>
    <button class="btn" id="tailorDl" style="display:none">Download .md</button>
    <button class="btn p" id="tailorCopy">Copy</button>
  </div>
</dialog>

<dialog id="dataDlg">
  <div class="dlg-h">
    <h2>Stored data</h2>
    <button class="btn" id="dataClose">Close</button>
  </div>
  <div class="dlg-b" id="dataBody"></div>
  <div class="dlg-f">
    <span id="dataHint" style="flex:1;color:var(--dim);font-size:12.5px"></span>
    <button class="btn" id="refreshData">Refresh</button>
  </div>
</dialog>
<div id="toast"></div>

<script id="payload" type="application/json">__DATA__</script>
<script>
const DATA = JSON.parse(document.getElementById('payload').textContent);
const ALL = DATA.newJobs.concat(DATA.oldJobs);
const KEY = 'jobbait.applied';
let applied;
try { applied = new Set(JSON.parse(localStorage.getItem(KEY) || '[]')); }
catch { applied = new Set(); }
const state = { tab:'new', srcs:null, q:'', min:50, jr:false,
                my: DATA.maxExperienceYears || 3,
                fresh: DATA.freshnessDays || 3, ai: true, aiPlan: true,
                queries: []};
document.getElementById('my').value = String(state.my);

document.getElementById('when').textContent =
  'generated ' + new Date(DATA.generatedAt).toLocaleString();

/* ---------- header ---------- */
function paintStats(){
  const hi = ALL.filter(j => j.score >= 90).length;
  $('stats').innerHTML = [
    ['New today', DATA.newJobs.length],
    ['Already shared', DATA.oldJobs.length],
    ['Score 90+', hi],
    ['Fresher-friendly', ALL.filter(isJunior).length],
    ['Sources', Object.keys(DATA.stats).length],
  ].map(([k,v]) => '<div class="stat"><b>'+v+'</b>'+k+'</div>').join('');
}

/* ============================================================
   Run controls. Only active when served by serve.py; when the
   file is opened directly from disk the buttons explain why.
   ============================================================ */
const LIVE = location.protocol !== 'file:';
const $ = id => document.getElementById(id);
let poller = null, logAt = 0, pending = null;

if (!LIVE){
  $('runBtn').textContent = 'Run scrape';
  $('runBtn').title = 'Start the local server to enable this';
  $('runBtn').disabled = true;
  $('statusTxt').textContent = 'open file - run unavailable';
  $('statusPill').classList.add('bad');
}

let saveTimer = null;
function saveSettings(patch){
  if (!LIVE) return;                 // file:// has no backend to ask
  clearTimeout(saveTimer);
  saveTimer = setTimeout(async () => {
    try { await postSettings(patch); toast('Saved - the next scrape uses these'); }
    catch (e) { toast('Could not save: ' + e.message, true); }
  }, 500);
}
async function postSettings(patch){
  const r = await api('/api/settings', {method:'POST',
    headers:{'Content-Type':'application/json'}, body: JSON.stringify(patch)});
  const st = r.settings || {};
  if (st.cities) { city.picked = st.cities; paintCityChip(); }
  if (st.queries) paintQueries(st.queries, false);
  return st;
}
/* Every control in the right-hand form -> one settings object. */
function formSnapshot(){
  return {
    cities: city.all ? [] : city.picked,
    minScore: state.min,
    maxExperienceYears: state.my,
    freshnessDays: state.fresh,
    aiScreening: state.ai,
    aiQueryPlan: state.aiPlan,
    sources: srcPicked(),
  };
}
function paintQueries(qs, planned){
  const box = $('qList');
  if (!box) return;
  state.queries = qs || [];
  box.innerHTML = state.queries.length
    ? '<div style="margin-top:6px">Search phrases for Apify:'
      + state.queries.map(q => '<span class="q">'+esc(q)+'</span>').join('')
      + '</div>'
    : '<div style="margin-top:6px;opacity:.7">Using the default queries from config.json.</div>';
  if (planned) toast('Ollama wrote the search phrases for this form');
}
async function api(path, opts, timeoutMs){
  const ctrl = timeoutMs ? new AbortController() : null;
  const t = ctrl ? setTimeout(() => ctrl.abort(), timeoutMs * 1000) : 0;
  try {
    const r = await fetch(path, ctrl ? {...opts, signal: ctrl.signal} : opts);
    const txt = await r.text();
    let data; try { data = JSON.parse(txt); } catch { data = {raw: txt}; }
    if (!r.ok) throw new Error(data.error || data.message || ('HTTP ' + r.status));
    return data;
  } catch (e) {
    if (e.name === 'AbortError')
      throw new Error('that request took too long - is Ollama running?');
    throw e;
  } finally { if (t) clearTimeout(t); }
}

function setRunning(on){
  $('runBtn').disabled = on || !LIVE;
  $('stopBtn').style.display = on ? '' : 'none';
  $('runBtn').style.display = on ? 'none' : '';
  $('dot').classList.toggle('on', on);
  $('statusTxt').textContent = on ? 'scraping...' : 'idle';
  $('console').classList.toggle('show', on || $('console').dataset.sticky === '1');
  $('statusPill').classList.toggle('bad', false);
}

function logLine(s){
  const el = $('console');
  const d = document.createElement('div');
  if (/error|failed|traceback|exception/i.test(s)) d.className = 'e';
  else if (/aborting|hard limit|refus|warning/i.test(s)) d.className = 'w';
  else if (/finished|-> \d+ new|writes|complete/i.test(s)) d.className = 'g';
  d.textContent = s;
  el.appendChild(d);
  while (el.children.length > 400) el.removeChild(el.firstChild);
  el.scrollTop = el.scrollHeight;
}

$('runBtn').onclick = async () => {
  $('runBtn').disabled = true;
  $('confirm').classList.add('show');
  let step = 'saving filters';
  try {
    // 1. the whole form goes to the server first, so the run matches the panel
    $('confirmTxt').textContent = 'Saving your filters...';
    await postSettings(formSnapshot());
    // 2. Ollama rewrites the search keywords from those filters
    if (state.aiPlan) {
      step = 'asking Ollama for search phrases (/api/queries/plan)';
      $('confirmTxt').textContent = 'Asking Ollama to write the search phrases...';
      try {
        const q = await api('/api/queries/plan', {method:'POST', body: '{}'});
        if (q.ok) paintQueries(q.queries, true);
      } catch (e) { logLine('WARN query planning: ' + e.message); }
    }
    // 3. cost check, now priced with the rewritten keywords
    step = 'checking cost (/api/preflight)';
    $('confirmTxt').textContent = 'Checking the next run cost...';
    const pf = await api('/api/preflight', {method:'POST'});
    const c = pf.credit;
    $('confirmTxt').innerHTML =
      (pf.runs + ' scraper runs across ' + pf.sources + ' sources'
        + ((pf.sourceNames || []).length
          ? ' (' + pf.sourceNames.map(esc).join(', ') + ')' : '')
        + ' &nbsp;&middot;&nbsp; '
        + '<b>~$' + pf.est.toFixed(3) + '</b> at most') +
      (c ? ' &nbsp;&middot;&nbsp; $' + c.remaining.toFixed(2) + ' left of $'
           + c.cap.toFixed(2) + ' (cycle ends ' + c.cycleEnd + ')' : '') +
      (pf.warning ? '<br><span style="color:var(--amber)">' + pf.warning + '</span>' : '');
    $('goBtn').textContent = pf.ok ? 'Run now' : 'Cannot run';
    $('goBtn').disabled = !pf.ok;
    pending = pf.ok;
  } catch (e){
    $('confirmTxt').innerHTML = 'Could not reach the server while ' + esc(step)
      + ': ' + esc(e.message)
      + '<br>Start it with <b>python serve.py</b>, then open '
      + '<b>http://127.0.0.1:8787</b> (not the file from disk).';
    $('goBtn').style.display = 'none';
    pending = false;
  }
  $('runBtn').disabled = false;
};

$('cancelBtn').onclick = () => { $('confirm').classList.remove('show'); };
$('goBtn').onclick = async () => {
  if (!pending) return;
  $('confirm').classList.remove('show');
  $('console').dataset.sticky = '0';
  $('console').innerHTML = '';
  logAt = 0;
  setRunning(true);
  try {
    await api('/api/run', {method:'POST'});
    poll();
  } catch (e){
    logLine('ERROR ' + e.message);
    setRunning(false);
  }
};
$('stopBtn').onclick = async () => {
  try { await api('/api/stop', {method:'POST'}); } catch {}
};

function paintRunState(st){
  if (st.elapsed) $('statusTxt').textContent = 'scraping... ' + st.elapsed + 's';
  if (!st.running && poller){
    clearInterval(poller); poller = null;
    $('statusTxt').textContent = st.exitCode === 0 ? 'finished' : 'failed';
    if (st.exitCode) $('statusPill').classList.add('bad');
    setRunning(false);
    refresh();
  }
}

async function poll(){
  if (poller) clearInterval(poller);
  poller = setInterval(async () => {
    try{
      const r = await api('/api/log?since=' + logAt);
      r.lines.forEach(logLine);
      logAt += r.lines.length;
      paintRunState(r.state);
    } catch(e){ clearInterval(poller); poller = null; setRunning(false); }
  }, 700);
}

async function refresh(){
  try{
    const d = await api('/api/jobs');
    DATA.newJobs = d.jobs || [];
    DATA.oldJobs = d.previouslyShown || [];
    DATA.stats = d.stats || {};
    DATA.generatedAt = d.generatedAt || DATA.generatedAt;
    ALL.length = 0; ALL.push(...DATA.newJobs, ...DATA.oldJobs);
    $('when').textContent = 'generated ' + new Date(DATA.generatedAt).toLocaleString();
    paintStats();
    paint();
  } catch {}
}


/* ============================================================
   Data manager: delete stored data section by section.
   ============================================================ */
const SECTIONS = {
  raw:     {name:'Raw scraper payloads', hint:'Saved API responses in out/raw. Only needed for --replay; a new run rebuilds them.'},
  ledger:  {name:'Seen-jobs ledger',     hint:'The dedup memory. Clearing it means old links can be shown to you again.'},
  reports: {name:'Daily reports',        hint:'Generated out/*.md and out/*.json files.'},
};
let picked = null, ds = null;

const toast = (msg, bad) => {
  const t = $('toast');
  t.textContent = msg;
  t.className = 'show' + (bad ? ' err' : '');
  setTimeout(() => t.className = '', 2600);
};
const mb = b => b < 1024 ? b + ' B'
  : b < 1048576 ? (b/1024).toFixed(1) + ' KB' : (b/1048576).toFixed(1) + ' MB';

function renderData(){
  if (!ds) return;
  const raw = ds.raw, seen = ds.seen, rep = ds.reports;
  $('dataBody').innerHTML =
    '<div class="sec"><div class="row"><div><b>' + SECTIONS.raw.name + '</b></div>' +
      '<button class="btn danger" data-act="del" data-scope="raw">Delete…</button></div>' +
      '<p class="sub">' + raw.count + ' files, ' + mb(raw.bytes) +
      ' &middot; oldest ' + raw.oldest + ' days &middot; ' + raw.older7 + ' older than 7d' +
      '<br>Saved API responses in <code>out/raw</code>. Only needed for ' +
      '<code>--replay</code>; a fresh run rebuilds them.</p>' +
      '<select id="rawAge"><option value="">All files</option>' +
        '<option value="1">Older than 1 day</option>' +
        '<option value="7">Older than 7 days</option>' +
        '<option value="30">Older than 30 days</option></select></div>' +

    '<div class="sec"><div class="row"><div><b>' + SECTIONS.ledger.name + '</b></div>' +
      '<button class="btn danger" data-act="del" data-scope="ledger">Delete…</button></div>' +
      '<p class="sub">' + seen.count + ' entries &middot; the dedup memory<br>' +
      SECTIONS.ledger.hint + '</p></div>' +

    '<div class="sec"><div class="row"><div><b>' + SECTIONS.reports.name + '</b></div>' +
      '<button class="btn danger" data-act="del" data-scope="reports">Delete…</button></div>' +
      '<p class="sub">' + rep.count + ' files, ' + mb(rep.bytes) + '</p></div>' +

    '<div class="sec"><div class="row"><div><b>Applied markers</b></div>' +
      '<button class="btn" data-act="clearApplied">Clear</button></div>' +
      '<p class="sub">The "applied" ticks saved in this browser only.</p></div>';
}

async function loadData(){
  try{
    ds = await api('/api/data');
    renderData();
    $('dataHint').textContent = 'Deleting is permanent and cannot be undone.';
  } catch(e){ toast('Could not read data status: ' + e.message, true); }
}

$('dataBtn').onclick = () => { $('dataDlg').showModal(); loadData(); };
$('dataClose').onclick = () => $('dataDlg').close();
$('refreshData').onclick = loadData;

$('dataBody').addEventListener('click', async e => {
  const btn = e.target.closest('[data-act]'); if (!btn) return;

  if (btn.dataset.act === 'clearApplied'){
    applied.clear();
    localStorage.removeItem(KEY);
    paint();
    toast('Applied markers cleared');
    return;
  }

  const scope = btn.dataset.scope;
  const ageSel = scope === 'raw' ? $('rawAge') : null;
  const older = ageSel && ageSel.value ? +ageSel.value : null;

  const what = scope === 'raw'
    ? (older ? 'all raw payloads older than ' + older + ' day(s)' : 'ALL raw payloads')
    : scope === 'ledger' ? 'the entire seen-jobs ledger'
    : 'all daily reports';

  const extra = scope === 'ledger'
    ? '\n\nAfter this, previously-shown links can appear again.'
    : '';

  if (!confirm('Delete ' + what + '?\n\nThis is permanent.' + extra)) return;
  if (scope !== 'ledger' && !confirm('Really delete ' + what + '?')) return;

  btn.disabled = true;
  try{
    const r = await api('/api/data/delete',
      {method:'POST', headers:{'Content-Type':'application/json'},
       body: JSON.stringify({scope, olderDays: older})});
    ds = r.status;
    renderData();
    if (scope === 'ledger'){
      DATA.newJobs = []; DATA.oldJobs = []; ALL.length = 0;
      paintStats(); paint();
    }
    toast('Deleted ' + what);
  } catch(err){
    toast('Delete failed: ' + err.message, true);
    btn.disabled = false;
  }
});

/* ---------- filter controls ---------- */
/* The picker lists every scraper config.json wires up, even one with no
   results yet. Ticking chooses BOTH what the cards show AND what the next
   paid run scrapes (null = every scraper). */
const SOURCES = (DATA.sourceNames && DATA.sourceNames.length
  ? DATA.sourceNames.slice()
  : [...new Set(ALL.map(j => j.source))]).sort();
function srcPicked(){ return state.srcs || SOURCES.slice(); }
document.getElementById('srcs').innerHTML = SOURCES.map(s =>
  '<div class="chip" data-src="'+s+'" role="button" tabindex="0">'+s+'</div>').join('');
function paintSrcs(){
  document.querySelectorAll('[data-src]').forEach(el =>
    el.classList.toggle('on', !state.srcs || state.srcs.includes(el.dataset.src)));
  document.getElementById('srcAll').classList.toggle('on', !state.srcs);
  const n = srcPicked().length;
  $('srcHint').textContent = !state.srcs
    ? 'Tick the boards the next paid run may scrape. Unticking all but one runs a single-scraper hunt.'
    : 'Showing + scraping: ' + srcPicked().join(', ') + '.';
}
document.querySelectorAll('[data-src]').forEach(el => {
  const toggle = () => {
    const s = el.dataset.src;
    let cur = srcPicked();
    cur = cur.includes(s) ? cur.filter(x => x !== s) : cur.concat(s);
    if (!cur.length) { toast('Keep at least one scraper ticked', true); return; }
    state.srcs = cur.length >= SOURCES.length ? null : cur;
    saveSettings({sources: srcPicked()});
    paintSrcs(); paint();
  };
  el.onclick = toggle;
  el.onkeydown = e => {
    if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); toggle(); }
  };
});
document.getElementById('srcAll').onclick = () => {
  state.srcs = null;
  saveSettings({sources: SOURCES.slice()});
  paintSrcs(); paint();
};
document.getElementById('jr').onclick = e => { state.jr=!state.jr; paint(); };
document.getElementById('q').oninput = e => { state.q=e.target.value.toLowerCase(); paint(); };
document.getElementById('ms').oninput = e => {
  state.min = +e.target.value; document.getElementById('msv').textContent = e.target.value;
  saveSettings({minScore: state.min}); paint();
};
document.getElementById('sort').onchange = paint;
document.getElementById('my').onchange = e => {
  state.my = +e.target.value;
  saveSettings({maxExperienceYears: state.my === 5 ? 5 : state.my}); paint();
};
document.getElementById('fresh').onchange = e => {
  state.fresh = +e.target.value;
  saveSettings({freshnessDays: state.fresh}); paint();
};
document.getElementById('aiChip').onclick = e => {
  state.ai = !state.ai;
  e.currentTarget.classList.toggle('on', state.ai);
  saveSettings({aiScreening: state.ai});
  toast(state.ai ? 'AI screening on - the next run asks Ollama about unstated experience'
                 : 'AI screening off - next run uses regex only');
};
document.getElementById('aiPlanChip').onclick = e => {
  state.aiPlan = !state.aiPlan;
  e.currentTarget.classList.toggle('on', state.aiPlan);
  saveSettings({aiQueryPlan: state.aiPlan});
  toast(state.aiPlan ? 'Ollama will rewrite the Apify search phrases on every run'
                     : 'Search phrases stay as configured in config.json');
  if (state.aiPlan) planQueries();     // show what it would search for now
};
async function planQueries(){
  if (!LIVE) return;
  $('qList').innerHTML = '<div style="margin-top:6px">Asking Ollama...</div>';
  try {
    const r = await api('/api/queries/plan', {method:'POST', body: '{}'});
    if (r.ok) paintQueries(r.queries, true);
    else { paintQueries([], false); toast(r.error || 'no queries', true); }
  } catch (e) {
    paintQueries([], false);
    toast('Query planning failed: ' + e.message, true);
  }
}
document.querySelectorAll('.tab').forEach(el => el.onclick = () => {
  state.tab = el.dataset.tab;
  document.querySelectorAll('.tab').forEach(t => t.classList.toggle('on', t===el));
  paint();
});

$('filters').addEventListener('submit', e => e.preventDefault());
document.querySelectorAll('#side .chip[role=button]').forEach(c => {
  c.addEventListener('keydown', e => {
    if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); c.click(); }
  });
});

/* ---------- helpers ---------- */
function parseExp(t){
  t = (t||'').toLowerCase().trim();
  if (!t) return null;
  let m = t.match(/(\d+)\s*[-–]\s*(\d+)/);
  if (m) return [+m[1], +m[2]];
  m = t.match(/(\d+)\s*\+/);
  if (m) return [+m[1], 99];
  m = t.match(/(?:up\s*to\s*)?(\d+)\s*(?:yr|year)/);
  if (m) return [0, +m[1]];
  if (/fresh|entry[- ]level|graduate|no\s+experience/.test(t)) return [0, 0];
  return null;
}
// Same rule as hunt.experience_ok(): open to 0-1 yrs, or the whole range fits.
function isJunior(j){
  const b = parseExp(j.experience);
  return !b || b[0] <= 1 || b[1] <= state.my;
}
function isLocal(j){
  const L = (j.location||'').toLowerCase();
  return /bhubaneswar|berhampur|odisha|bhubaneshwar|sambalpur|balasore|rourkela|hyderabad/.test(L)
      || /remote|hybrid|work from home|wfh/.test(L);
}
const esc = s => String(s==null?'':s).replace(/[&<>"]/g,
  c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));

/* ---------- render ---------- */
function card(j){
  const link = j.apply_url || j.board_url;
  const cls = j.score >= 90 ? 's-hi' : j.score >= 80 ? 's-mid' : 's-lo';
  const chips = (j.matched||[]).map(t => '<span class="t ok">'+esc(t)+'</span>')
    .concat((j.missing||[]).map(t => '<span class="t gap">'+esc(t)+'</span>')).join('');
  const age = Math.max(0, Math.round((Date.now() - new Date(j.posted_at)) / 36e5));
  const when = age < 24 ? age+'h ago' : Math.round(age/24)+'d ago';

  const meta = [
    j.location ? '<span class="m">'+esc(j.location)+'</span>' : '',
    isLocal(j) ? '<span class="m local">'+esc(j.location||'remote')+'</span>' : '',
    j.experience ? '<span class="m'+(isJunior(j)?' jr':'')+'">'+esc(j.experience)+'</span>'
                  : '<span class="m jr">no bar stated</span>',
    j.salary ? '<span class="m">'+esc(j.salary)+'</span>' : '',
    j.posted_at ? '<span class="m fresh">posted '+when+'</span>' : '',
    (j.resumeHits||[]).length
      ? '<span class="m local" title="Mentions skills from your resume">resume '
        + j.resumeHits.length + '/' + (DATA.resumeSkills||[]).length + '</span>' : '',
    j.ai ? '<span class="m'+(j.ai.fresher_friendly?' local':'')+'" title="'
        + esc(j.ai.reason||'') + '">AI: ' + (j.ai.fresher_friendly
        ? 'fresher ok' : 'not for fresher') + '</span>' : '',
    '<span class="m">'+esc(j.source)+'</span>',
  ].join('');

  const extra = (j.board_url && j.board_url !== j.apply_url)
    ? '<a class="btn" href="'+esc(j.board_url)+'" target="_blank" rel="noopener">Board link</a>' : '';
  const isOn = applied.has(j.key);

  return '<div class="card'+(isOn?' applied':'')+'" data-key="'+esc(j.key)+'">'+
    '<div class="top"><div><div class="ttl">'+esc(j.title)+'</div>'+
      '<div class="co">'+esc(j.company||'(company hidden)')+'</div></div>'+
      '<div class="score '+cls+'">'+j.score+'</div></div>'+
    '<div class="meta">'+meta+'</div>'+
    '<div class="tags">'+chips+'</div>'+
    (j.excerpt ? '<details><summary>Role text</summary><div class="co" style="margin-top:7px">'+
      esc(j.excerpt)+'</div></details>' : '')+
    '<div class="acts">'+
      '<a class="btn p" href="'+esc(link)+'" target="_blank" rel="noopener">Open job</a>'+extra+
      '<button class="btn" data-act="copy">Copy link</button>'+
      '<button class="btn" data-act="apply">'+(isOn?'Applied ✓':'Mark applied')+'</button>'+
      '<button class="btn p" data-act="tailor" title="Rewrite your resume for this exact job">Tailor resume</button>'+
    '</div></div>';
}

function paint(){
  let rows = state.tab === 'new' ? DATA.newJobs
           : state.tab === 'old' ? DATA.oldJobs : ALL;
  rows = rows.filter(j =>
    (!state.srcs || state.srcs.includes(j.source)) &&
    inCities(j) && inFresh(j) && j.score >= state.min &&
    (!state.jr || isJunior(j)) &&
    (!state.q || (j.title + ' ' + j.company + ' ' + j.location + ' ' +
      (j.excerpt||'') + ' ' + (j.matched||[]).join(' ')).toLowerCase().includes(state.q))
  );
  const s = document.getElementById('sort').value;
  rows.sort((a,b) => s === 'score'  ? b.score - a.score
                 : s === 'new'    ? new Date(b.posted_at) - new Date(a.posted_at)
                 : s === 'old'    ? new Date(a.posted_at) - new Date(b.posted_at)
                 : a.company.localeCompare(b.company));

  document.getElementById('grid').innerHTML = rows.map(card).join('');
  document.getElementById('count').textContent =
    rows.length + ' job' + (rows.length === 1 ? '' : 's') +
    '  (of ' + ALL.length + ' collected)';
  document.getElementById('empty').style.display = rows.length ? 'none' : 'block';

  paintSrcs();
  document.getElementById('jr').classList.toggle('on', state.jr);
}

document.getElementById('grid').addEventListener('click', e => {
  const btn = e.target.closest('[data-act]'); if (!btn) return;
  const key = btn.closest('.card').dataset.key;
  const j = ALL.find(x => x.key === key);
  if (btn.dataset.act === 'tailor') {
    if (!LIVE) { toast('Start the server (python serve.py) to generate', true); return; }
    _openTailor(key);
    return;
  }
  if (btn.dataset.act === 'apply') {
    applied.has(key) ? applied.delete(key) : applied.add(key);
    localStorage.setItem(KEY, JSON.stringify([...applied]));
    paint();
  } else {
    const url = j.apply_url || j.board_url;
    navigator.clipboard.writeText(url).then(() => {
      btn.textContent = 'Copied ✓';
      setTimeout(() => btn.textContent = 'Copy link', 1400);
    });
  }
});

/* why the rest were dropped */
const why = Object.entries(DATA.stats).flatMap(([src, v]) =>
  Object.entries(v).filter(([k]) => !['raw','new','errors'].includes(k))
    .map(([k, n]) => src + ': ' + n + ' ' + k.replace(/-/g, ' ')));
document.getElementById('why').innerHTML =
  '<b>Why the other ' + (DATA.scraped - ALL.length) + ' results were filtered out</b><br>' +
  (why.length ? why.map(esc).join(' &nbsp;&middot;&nbsp; ')
              : 'none') +
  '<br><br>Thresholds: posted within ' + DATA.freshnessDays + ' days, match score &gt;= ' +
  DATA.minScore + '. Click a job to open it; "Mark applied" is remembered in this browser.';

/* ============================================================
   Location: multi-city checkbox picker + client-side filtering.
   ============================================================ */
const HUBS = (DATA.hubAliases || []).map(([n, a]) => ({name:n, alias:a.map(x=>x.toLowerCase())}));
const city = { picked: [], all: false };

function inFresh(j){
  if (state.fresh >= 14) return true;          // 14 = keep everything shown
  const t = new Date(j.posted_at).getTime();
  if (!t) return true;
  return (Date.now() - t) / 864e5 <= state.fresh;
}
function inCities(j){
  if (LIVE === false) { /* file:// still filters, data is embedded */ }
  if (city.all || !city.picked.length) return true;
  const loc = ((j.location || '') + ' ' + (j.excerpt || '')).toLowerCase();
  if (/remote|work from home|wfh|anywhere in india/.test(loc)) return true;
  if (!loc.trim()) return false;            // unknown place: hidden while a city filter is on
  return city.picked.some(c => {
    const h = HUBS.find(x => x.name === c);
    if (!h) return loc.includes(c.toLowerCase());
    return h.alias.some(a => loc.includes(a));
  });
}

function renderHubList(){
  const q = ($('locSearch').value || '').toLowerCase().trim();
  const list = HUBS.filter(h => !q || h.alias.some(a => a.includes(q)) ||
                                h.name.toLowerCase().includes(q));
  $('locList').innerHTML = list.length
    ? list.map(h => '<label><input type="checkbox" value="'+esc(h.name)+'"'
        + (city.picked.includes(h.name) ? ' checked' : '') + '>'
        + esc(h.name) + '</label>').join('')
    : '<div class="none">No IT hub matches that search.</div>';
  $('locHint').textContent = city.picked.length
    ? city.picked.length + ' cit' + (city.picked.length === 1 ? 'y' : 'ies') + ' selected'
    : (city.all ? 'All India' : 'No city picked - scraping the config defaults');
}

$('locSearch').oninput = renderHubList;
$('locList').addEventListener('change', e => {
  const v = e.target.value;
  if (e.target.checked) { if (!city.picked.includes(v)) city.picked.push(v); }
  else city.picked = city.picked.filter(c => c !== v);
  renderHubList();
});
$('locAll').onchange = e => { city.all = e.target.checked; renderHubList(); };
$('locClear').onclick = () => { city.picked = []; city.all = false; $('locAll').checked = false; renderHubList(); };
$('locSave').onclick = async () => {
  try {
    await api('/api/settings', {method:'POST',
      headers:{'Content-Type':'application/json'},
      body: JSON.stringify({cities: city.all ? [] : city.picked})});
    paintCityChip();
    paint();
    toast(city.all ? 'Scraping all India' : (city.picked.length
      ? 'Hunting in ' + city.picked.join(', ') : 'City filter cleared'));
  } catch (e) { toast('Could not save: ' + e.message, true); }
};

function paintCityChip(){
  const on = !city.all && city.picked.length > 0;
  const h = $('locHint');
  if (!h) return;
  h.innerHTML = on
    ? '<b>' + (city.picked.length > 2 ? city.picked.length + ' cities' : city.picked.join(', ')) + '</b> '
      + '<span style="opacity:.7">(tick more above, then Save cities)</span>'
    : (city.all ? '<b>All India</b>' : 'No city picked - scraping the config defaults');
}

/* ============================================================
   Resume upload: skills feed a +5 point bonus in hunt.py.
   ============================================================ */
let resumeSkills = [];

function paintResume(){
  $('resumeBtn').classList.toggle('on', resumeSkills.length > 0);
  $('resumeBtn').textContent = resumeSkills.length
    ? 'Resume: ' + resumeSkills.length + ' skills' : 'Resume';
  $('resumeDel').style.display = resumeSkills.length ? '' : 'none';
  $('resumeOut').innerHTML = resumeSkills.length
    ? '<span style="color:var(--dim)">Skills found:</span> '
      + resumeSkills.map(t => '<span class="t ok">'+esc(t)+'</span>').join('')
      + '<div style="color:var(--dim);margin-top:6px">These add up to +5 points '
      + 'to jobs that mention them.</div>'
    : '';
  DATA.resumeSkills = resumeSkills;
}

/* profile form <-> plain objects; every field is editable by hand */
function fillProfile(p){
  p = p || {};
  $('pfName').value = p.name || '';
  $('pfPhone').value = p.phone || '';
  $('pfEmail').value = p.email || '';
  $('pfLoc').value = p.location || '';
  $('pfSummary').value = p.summary || '';
  $('pfSkills').value = (p.skills || resumeSkills).join(', ');
  $('pfEdu').value = (p.education || []).join('\n');
  $('pfProj').value = (p.projects || []).join('\n');
  $('pfCert').value = (p.certifications || []).join('\n');
  $('pfStrength').value = (p.strengths || []).join('\n');
}
function readProfile(){
  const lines = v => v.split('\n').map(s => s.trim()).filter(Boolean);
  return {
    name: $('pfName').value.trim(), phone: $('pfPhone').value.trim(),
    email: $('pfEmail').value.trim(), location: $('pfLoc').value.trim(),
    summary: $('pfSummary').value.trim(),
    skills: $('pfSkills').value.split(',').map(s => s.trim()).filter(Boolean),
    education: lines($('pfEdu').value), projects: lines($('pfProj').value),
    certifications: lines($('pfCert').value), strengths: lines($('pfStrength').value),
  };
}
function showAuto(msg){
  const el = $('autoNote');
  if (!msg) { el.style.display = 'none'; return; }
  el.style.display = 'block';
  el.innerHTML = '<b>Ollama</b> ' + esc(msg);
}
function openResume(){
  if (resumeProfile) fillProfile(resumeProfile);
  $('resumeDlg').showModal();
}
let resumeProfile = {};

async function saveProfile(toastMsg){
  const body = {profile: readProfile(),
                text: $('resumeText').value.trim()};
  if (!body.profile.skills.length && !body.profile.summary && !body.text) {
    toast('Nothing to save yet - upload or paste first', true); return;
  }
  const r = await api('/api/resume/profile', {method:'POST',
    headers:{'Content-Type':'application/json'},
    body: JSON.stringify(body)}, 300);
  resumeSkills = r.resume.skills || [];
  resumeProfile = r.profile || {};
  fillProfile(resumeProfile);
  paintResume(); paint();
  applySearchTuning(r);
  toast(toastMsg || ('Saved: ' + resumeSkills.length + ' skills'));
  await tuneAndNote('Saved.');
}

/* The save itself answers fast now; query planning runs right after it, in
   its own request, so a slow model can never abort the save again. */
function applySearchTuning(r){
  const s = (r && r.search) || {};
  if (Array.isArray(s.cities) && s.cities.length) {
    city.picked = s.cities.slice();
    city.all = false;
    const la = $('locAll'); if (la) la.checked = false;
    renderHubList(); paintCityChip();
  }
}
async function tuneAndNote(base){
  showAuto(base + ' Tuning the scraper from your resume...');
  await planQueries();
  showAuto(base + ' ' + (state.queries.length
    ? 'Scraper tuned - the next run hunts '
      + state.queries.map(q => '\u201c' + q + '\u201d').join(', ')
      + '. Press Run scrape when ready.'
    : 'Scraper tuning came back empty - the run uses the default phrases.'));
}

$('resumeBtn').onclick = openResume;
$('resumeOpen').onclick = openResume;
$('resumeClose').onclick = () => { $('resumeDlg').close(); paint(); };
$('resumeFile').onchange = async e => {
  const f = e.target.files[0]; if (!f) return;
  if (f.size > 8 * 1024 * 1024) { toast('That file is over 8 MB', true); e.target.value=''; return; }
  $('fileHint').textContent = 'Reading ' + f.name + ' - Ollama is splitting it into sections...';
  showAuto('is reading ' + f.name + ' and filling every section below - this takes a few minutes on first read, the dialog stays usable.');
  try {
    const buf = await f.arrayBuffer();
    let bin = ''; const u8 = new Uint8Array(buf);
    for (let i = 0; i < u8.length; i += 0x8000)
      bin += String.fromCharCode.apply(null, u8.subarray(i, i + 0x8000));
    const r = await api('/api/resume/file', {method:'POST',
      headers:{'Content-Type':'application/json'},
      body: JSON.stringify({name: f.name, data: btoa(bin)})}, 560);
    resumeSkills = r.resume.skills || [];
    resumeProfile = r.resume.profile || r.profile || {};
    $('resumeText').value = r.preview || r.resume.rawText || '';
    fillProfile(resumeProfile);
    paintResume(); paint();
    applySearchTuning(r);
    toast('Read ' + resumeSkills.length + ' skills from ' + f.name
      + (r.auto ? ' - sections auto-filled' : ''));
    $('fileHint').textContent = f.name + ' - ' + r.resume.chars
      + ' characters read. Every section below is editable - fix and Save.';
    await tuneAndNote(r.auto
      ? 'Ollama filled the sections below - check them and press Save resume.'
      : 'Ollama could not run this time (is it up?), so the form is empty - you can fill it yourself.');
    $('resumeFile').value = '';
  } catch (err) {
    toast(err.message, true);
    $('fileHint').textContent = err.message;
    showAuto('');
  }
};
$('resumeSave').onclick = async () => {
  const text = $('resumeText').value;
  if (text.trim().length < 40 && !readProfile().skills.length
      && !readProfile().summary) {
    toast('Upload a file, paste text, or fill a section first', true); return;
  }
  try {
    if (text.trim().length >= 40 && !resumeSkills.length) {
      const r = await api('/api/resume', {method:'POST',
        headers:{'Content-Type':'application/json'},
        body: JSON.stringify({text})}, 560);
      resumeSkills = r.resume.skills || [];
      resumeProfile = r.resume.profile || r.profile || {};
      fillProfile(resumeProfile);
      applySearchTuning(r);
      paintResume(); paint();
      toast('Resume saved: ' + resumeSkills.length + ' skills');
      await tuneAndNote('Saved.');
    } else {
      await saveProfile();
    }
    paintResume(); paint();
    $('resumeDlg').close();
  } catch (e) { toast(e.message, true); }
};
$('resumeDel').onclick = async () => {
  try {
    await api('/api/resume/delete', {method:'POST'});
    resumeSkills = []; resumeProfile = {};
    $('resumeText').value = ''; fillProfile({});
    showAuto(''); paintResume(); paint();
    toast('Resume removed');
  } catch (e) { toast(e.message, true); }
};

/* ============================================================
   AI status pill: shows the local Ollama model the run will use.
   ============================================================ */
async function paintAI(){
  try {
    const a = await api('/api/ai');
    window.__aiModel = a.model || '';
    const el = $('aiPill');
    if (a.available) {
      el.textContent = 'AI: ' + a.model;
      el.style.borderColor = 'var(--accent)';
      el.title = 'Local Ollama (' + a.url + ') answers the second opinion on '
               + 'jobs with no stated experience.';
    } else {
      el.textContent = 'AI: offline';
      el.style.borderColor = 'var(--red)';
      el.title = 'Ollama is not reachable - runs keep going but fall back to '
               + 'regex-only screening.';
    }
  } catch { $('aiPill').textContent = 'AI: ?'; }
}

async function loadSettings(){
  try {
    const st = await api('/api/settings');
    city.picked = st.cities || [];
    $('locAll').checked = city.all = false;
    paintCityChip();
    if (st.minScore != null) {
      state.min = st.minScore;
      $('ms').value = String(state.min);
      $('msv').textContent = state.min;
    }
    if (st.maxExperienceYears != null) {
      state.my = st.maxExperienceYears;
      $('my').value = String(state.my);
    }
    if (st.freshnessDays != null) {
      state.fresh = st.freshnessDays;
      $('fresh').value = String(state.fresh);
    }
    if (st.aiScreening != null) {
      state.ai = st.aiScreening;
      $('aiChip').classList.toggle('on', state.ai);
    }
    if (st.aiQueryPlan != null) {
      state.aiPlan = st.aiQueryPlan;
      $('aiPlanChip').classList.toggle('on', state.aiPlan);
    }
    if (Array.isArray(st.sources) && st.sources.length) {
      const known = st.sources.filter(s => SOURCES.includes(s));
      state.srcs = known.length >= SOURCES.length ? null : known;
      paintSrcs();
    }
    paintQueries(st.queries || [], false);
  } catch {}
  try {
    const r = await api('/api/resume');
    resumeSkills = r.skills || [];
    resumeProfile = r.profile || {};
    if (resumeSkills.length || resumeProfile.name) {
      fillProfile(resumeProfile);
      if (r.rawText) $('resumeText').value = r.rawText;
    }
    paintResume();
  } catch {}
  paintAI();
}

/* ============================================================
   Tailor resume / cover letter for one job, via local Ollama.
   ============================================================ */
const tstate = { key:null, kind:'resume', text:'', title:'' };

function paintTailor(){
  document.querySelectorAll('[data-ttab]').forEach(t =>
    t.classList.toggle('on', t.dataset.ttab === tstate.kind));
  const out = $('tailorOut');
  if (!tstate.text) {
    out.innerHTML = '<span class="spin">Generating with ' +
      esc((window.__aiModel) || 'the local AI model') +
      ' - first time takes up to a minute...</span>';
    return;
  }
  if (tstate.kind === 'cover') out.textContent = tstate.text;
  else out.innerHTML = renderMd(tstate.text);
  $('tailorDl').style.display = tstate.text ? '' : 'none';
  $('tailorCopy').style.display = tstate.text ? '' : 'none';
}

function renderMd(md){
  const esc2 = s => esc(s);
  let html = esc2(md)
    .replace(/^### (.*)$/gm, '<h3>$1</h3>')
    .replace(/^## (.*)$/gm, '<h3>$1</h3>')
    .replace(/^# (.*)$/gm, '<h2>$1</h2>')
    .replace(/\*\*([^*]+)\*\*/g, '<b>$1</b>')
    .replace(/^- (.*)$/gm, '<li>$1</li>')
    .replace(/(<li>[\s\S]*?<\/li>)(?!\s*<li>)/g, '<ul>$1</ul>')
    .replace(/^\s*$/gm, '<br>');
  return html.replace(/<br>(\s*<ul>)/g, '$1').replace(/<\/ul>(\s*<br>)/g, '</ul>');
}

async function loadTailor(kind){
  tstate.kind = kind;
  paintTailor();
  try {
    const r = await api('/api/tailor', {method:'POST',
      headers:{'Content-Type':'application/json'},
      body: JSON.stringify({key: tstate.key, kind})});
    if (!r.ok) {
      tstate.text = '';
      $('tailorOut').innerHTML = '<span style="color:var(--red)">' +
        esc(r.error || 'failed') + '</span>';
      $('tailorHint').textContent = r.error || '';
      return;
    }
    tstate.text = r.text;
    $('tailorHint').textContent = (r.cached ? 'Cached - generated earlier. ' : 'Generated now. ')
      + 'Read it before sending: it rewords your real facts, never adds any.';
    paintTailor();
  } catch (e) {
    $('tailorOut').innerHTML = '<span style="color:var(--red)">' + esc(e.message) + '</span>';
  }
}

document.querySelectorAll('[data-ttab]').forEach(el => el.onclick = () =>
  loadTailor(el.dataset.ttab));

$('tailorClose').onclick = () => $('tailorDlg').close();
$('tailorCopy').onclick = async () => {
  if (!tstate.text) return;
  await navigator.clipboard.writeText(tstate.text);
  $('tailorCopy').textContent = 'Copied ✓';
  setTimeout(() => $('tailorCopy').textContent = 'Copy', 1400);
};
$('tailorDl').onclick = () => {
  if (!tstate.text) return;
  const blob = new Blob([tstate.text], {type:'text/markdown'});
  const a = document.createElement('a');
  a.href = URL.createObjectURL(blob);
  a.download = (tstate.kind === 'cover' ? 'cover-letter-' : 'resume-') +
    (tstate.title || 'job').replace(/[^a-z0-9]+/gi, '-').toLowerCase().slice(0, 40) + '.md';
  a.click();
  URL.revokeObjectURL(a.href);
};

const _openTailor = async key => {
  const j = ALL.find(x => x.key === key) || {};
  tstate.key = key; tstate.title = j.title || 'job'; tstate.text = '';
  $('tailorTitle').textContent = 'Tailored application - ' + (j.title || '');
  $('tailorHint').textContent = j.resumeHits && j.resumeHits.length
    ? 'Your resume already matches this job on ' + j.resumeHits.length + ' skill(s).'
    : "This rewrites your resume to this posting's exact wording.";
  $('tailorDlg').showModal();
  await loadTailor('resume');
};

if (LIVE) loadSettings();

paintStats();
paint();
</script>
</body>
</html>
"""


def latest_report() -> Path | None:
    reports = sorted(OUT_DIR.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True)
    return reports[0] if reports else None


def build() -> Path:
    src = latest_report()
    d = json.loads(src.read_text(encoding="utf-8")) if src else {}
    try:
        cfg = json.loads((HERE / "config.json").read_text(encoding="utf-8"))
        source_names = list(cfg.get("sources", {}))
    except Exception:  # noqa: BLE001
        source_names = []

    data = {
        "generatedAt": d.get("generatedAt", ""),
        "freshnessDays": d.get("freshnessDays", 3),
        "minScore": d.get("minScore", 70),
        "maxExperienceYears": d.get("maxExperienceYears", 3),
        "stats": d.get("stats", {}),
        "hubAliases": [[n, list(a)] for n, a in hubs.HUBS],
        "sourceNames": source_names,
        "resumeSkills": d.get("resumeSkills", []),
        "newJobs": d.get("jobs", []),
        "oldJobs": d.get("previouslyShown", []),
        "scraped": sum(
            v.get("raw", 0) for v in d.get("stats", {}).values()
        ) or len(d.get("jobs", [])) + len(d.get("previouslyShown", [])),
    }
    # </script> inside the payload would terminate the tag early.
    blob = json.dumps(data, ensure_ascii=False).replace("<", "\\u003c")
    out = OUT_DIR / "dashboard.html"
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    out.write_text(TEMPLATE.replace("__DATA__", blob), encoding="utf-8")
    if not src:
        print(f"dashboard -> {out}  (no report yet - empty board)")
    else:
        print(f"dashboard -> {out}  ({len(data['newJobs'])} new, {len(data['oldJobs'])} earlier)")
    return out


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--open", action="store_true", help="open in the default browser")
    a = ap.parse_args()
    p = build()
    if a.open:
        webbrowser.open(p.resolve().as_uri())