"""
dashboard.py — Outreach Pipeline Web Dashboard
Run: python3 dashboard.py
Open: http://localhost:5001
"""

import csv
import os
from flask import Flask, render_template_string, abort
from markupsafe import Markup
from urllib.parse import quote, unquote

app = Flask(__name__)

DATA_DIR = os.path.join(os.path.dirname(__file__), "data")

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def data_path(filename):
    return os.path.join(DATA_DIR, filename)


def read_csv(filename):
    """Return list of dicts from a CSV file, or None if file missing."""
    path = data_path(filename)
    if not os.path.exists(path):
        return None
    with open(path, newline="", encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


def file_info(filename):
    """Return (size_str, row_count) for a CSV file."""
    path = data_path(filename)
    if not os.path.exists(path):
        return None, None
    size = os.path.getsize(path)
    if size < 1024:
        size_str = f"{size} B"
    elif size < 1024 * 1024:
        size_str = f"{size / 1024:.1f} KB"
    else:
        size_str = f"{size / (1024*1024):.1f} MB"
    rows = read_csv(filename)
    return size_str, len(rows) if rows is not None else 0


def truncate(text, length=80):
    if not text:
        return ""
    text = str(text).strip()
    return text[:length] + "…" if len(text) > length else text


def score_color(score):
    try:
        s = int(float(score))
    except (ValueError, TypeError):
        return "#888"
    if s >= 8:
        return "#2d6a4f"
    if s >= 6:
        return "#e07b39"
    return "#c0392b"


def bool_icon(val):
    if str(val).strip().lower() in ("true", "1", "yes"):
        return "✓"
    return "–"


def angle_from_summary(summary):
    """Derive a short angle label from analysis_summary."""
    if not summary:
        return "Unknown"
    s = summary.lower()
    if "education" in s or "opleiding" in s or "leren" in s:
        return "Education"
    if "plastic" in s:
        return "Plastic"
    if "community" in s or "gemeenschap" in s:
        return "Community"
    if "sustainability" in s or "duurzaamheid" in s:
        return "Sustainability"
    return "General"


ANGLE_COLORS = {
    "Education": "#1a6b9e",
    "Plastic": "#8e44ad",
    "Community": "#27ae60",
    "Sustainability": "#2d6a4f",
    "General": "#7f8c8d",
    "Unknown": "#bdc3c7",
}

# ---------------------------------------------------------------------------
# Base HTML template
# ---------------------------------------------------------------------------

BASE = """<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{{ title }} — Outreach Dashboard</title>
  <style>
    *, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }
    body {
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
      background: #f5f6f8;
      color: #1a1a2e;
      font-size: 15px;
      line-height: 1.5;
    }
    a { color: #2d6a4f; text-decoration: none; }
    a:hover { text-decoration: underline; }

    /* NAV */
    nav {
      background: #2d6a4f;
      padding: 0 24px;
      display: flex;
      align-items: center;
      gap: 0;
      height: 52px;
      position: sticky;
      top: 0;
      z-index: 100;
      box-shadow: 0 2px 6px rgba(0,0,0,.18);
    }
    nav .brand {
      color: #fff;
      font-weight: 700;
      font-size: 16px;
      margin-right: 32px;
      letter-spacing: .3px;
    }
    nav a {
      color: rgba(255,255,255,.82);
      padding: 0 14px;
      height: 52px;
      display: flex;
      align-items: center;
      font-size: 14px;
      font-weight: 500;
      transition: background .15s;
      text-decoration: none;
    }
    nav a:hover, nav a.active {
      background: rgba(255,255,255,.13);
      color: #fff;
    }

    /* LAYOUT */
    .page { max-width: 1100px; margin: 32px auto; padding: 0 20px 60px; }
    h1 { font-size: 22px; font-weight: 700; margin-bottom: 24px; color: #1a1a2e; }
    h2 { font-size: 17px; font-weight: 600; margin: 28px 0 12px; color: #2d3436; }

    /* CARDS */
    .cards { display: flex; flex-wrap: wrap; gap: 16px; margin-bottom: 32px; }
    .card {
      background: #fff;
      border-radius: 10px;
      padding: 20px 24px;
      min-width: 160px;
      flex: 1;
      box-shadow: 0 1px 4px rgba(0,0,0,.08);
    }
    .card .label { font-size: 12px; text-transform: uppercase; letter-spacing: .6px; color: #636e72; margin-bottom: 6px; }
    .card .value { font-size: 32px; font-weight: 700; color: #2d6a4f; }
    .card .sub { font-size: 12px; color: #888; margin-top: 4px; }

    /* ALERT */
    .alert {
      background: #fff3cd;
      border: 1px solid #ffc107;
      border-radius: 8px;
      padding: 16px 20px;
      color: #856404;
      margin-bottom: 24px;
    }

    /* TABLE */
    .table-wrap { overflow-x: auto; margin-bottom: 32px; }
    table { width: 100%; border-collapse: collapse; background: #fff; border-radius: 10px; overflow: hidden; box-shadow: 0 1px 4px rgba(0,0,0,.08); }
    th { background: #f0f4f2; color: #2d6a4f; font-size: 12px; text-transform: uppercase; letter-spacing: .5px; padding: 10px 14px; text-align: left; border-bottom: 2px solid #e0e9e5; }
    td { padding: 10px 14px; border-bottom: 1px solid #f0f0f0; vertical-align: top; font-size: 14px; }
    tr:last-child td { border-bottom: none; }
    tr:hover td { background: #f9fffe; }

    /* BADGE */
    .badge {
      display: inline-block;
      padding: 2px 9px;
      border-radius: 12px;
      font-size: 12px;
      font-weight: 600;
      color: #fff;
    }
    .score-pill {
      display: inline-block;
      padding: 2px 10px;
      border-radius: 12px;
      font-size: 13px;
      font-weight: 700;
      color: #fff;
    }
    .icon-check { color: #2d6a4f; font-weight: 700; }
    .icon-dash  { color: #bdc3c7; }

    /* BAR CHART */
    .bar-chart { margin-bottom: 32px; }
    .bar-row { display: flex; align-items: center; gap: 10px; margin-bottom: 8px; }
    .bar-label { width: 140px; font-size: 13px; flex-shrink: 0; }
    .bar-track { flex: 1; background: #e9ecef; border-radius: 4px; height: 20px; }
    .bar-fill  { height: 20px; border-radius: 4px; background: #2d6a4f; transition: width .3s; }
    .bar-count { width: 36px; font-size: 13px; text-align: right; flex-shrink: 0; color: #555; }

    /* DETAIL PAGE */
    .section-box {
      background: #fff;
      border-radius: 10px;
      padding: 24px;
      margin-bottom: 20px;
      box-shadow: 0 1px 4px rgba(0,0,0,.08);
    }
    .section-box h2 { margin-top: 0; }
    .info-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(220px, 1fr)); gap: 14px; }
    .info-item .key { font-size: 11px; text-transform: uppercase; letter-spacing: .5px; color: #888; margin-bottom: 3px; }
    .info-item .val { font-size: 15px; color: #1a1a2e; }
    textarea {
      width: 100%;
      border: 1px solid #dfe6e9;
      border-radius: 6px;
      padding: 12px;
      font-family: inherit;
      font-size: 13px;
      line-height: 1.6;
      resize: vertical;
      background: #f9fffe;
      color: #1a1a2e;
    }
    .char-count { font-size: 11px; color: #888; margin-top: 4px; text-align: right; }
    .field-label { font-size: 12px; text-transform: uppercase; letter-spacing: .5px; color: #636e72; margin-bottom: 6px; margin-top: 16px; }
    select {
      padding: 8px 14px;
      border: 1px solid #dfe6e9;
      border-radius: 6px;
      font-size: 14px;
      background: #fff;
      color: #1a1a2e;
    }
    .back-link { margin-bottom: 20px; display: inline-block; font-size: 13px; }

    /* LINKEDIN QUEUE */
    .day-group { margin-bottom: 36px; }
    .day-header {
      background: #2d6a4f;
      color: #fff;
      padding: 8px 16px;
      border-radius: 8px 8px 0 0;
      font-weight: 700;
      font-size: 14px;
    }
    .queue-item {
      background: #fff;
      border: 1px solid #e0e9e5;
      border-top: none;
      padding: 16px 20px;
    }
    .queue-item:last-child { border-radius: 0 0 8px 8px; }
    .queue-meta { font-size: 13px; color: #636e72; margin-bottom: 8px; }
    .queue-name { font-weight: 600; font-size: 15px; }

    /* EXPORT */
    .export-card {
      background: #fff;
      border-radius: 10px;
      padding: 20px 24px;
      display: flex;
      align-items: center;
      justify-content: space-between;
      margin-bottom: 14px;
      box-shadow: 0 1px 4px rgba(0,0,0,.08);
      flex-wrap: wrap;
      gap: 12px;
    }
    .export-info .name { font-weight: 600; font-size: 15px; }
    .export-info .meta { font-size: 12px; color: #888; margin-top: 3px; }
    .btn {
      display: inline-block;
      padding: 9px 20px;
      background: #2d6a4f;
      color: #fff;
      border-radius: 6px;
      font-size: 14px;
      font-weight: 600;
      text-decoration: none;
    }
    .btn:hover { background: #245a42; color: #fff; text-decoration: none; }
    .btn-disabled {
      background: #b2bec3;
      cursor: not-allowed;
    }

    @media (max-width: 640px) {
      .cards { flex-direction: column; }
      .bar-label { width: 100px; }
    }
  </style>
</head>
<body>
<nav>
  <span class="brand">Outreach Pipeline</span>
  <a href="/" class="{{ 'active' if active == 'home' else '' }}">Overview</a>
  <a href="/linkedin" class="{{ 'active' if active == 'linkedin' else '' }}">LinkedIn Queue</a>
  <a href="/export" class="{{ 'active' if active == 'export' else '' }}">Export</a>
</nav>
<div class="page">
  {{ content }}
</div>
</body>
</html>"""


def render_page(title, active, content):
    return render_template_string(
        BASE, title=title, active=active, content=Markup(content)
    )


# ---------------------------------------------------------------------------
# Home / Overview
# ---------------------------------------------------------------------------

@app.route("/")
def home():
    companies   = read_csv("companies.csv")
    csr         = read_csv("csr_analysis.csv")
    contacts    = read_csv("contacts.csv")
    drafts      = read_csv("outreach_drafts.csv")

    if companies is None and csr is None:
        content = '<div class="alert">No data found. Run the pipeline first to generate CSVs in <code>data/</code>.</div>'
        return render_page("Overview", "home", content)

    total       = len(companies) if companies else 0
    csr_found   = len(csr) if csr else 0
    relevant    = sum(1 for r in (csr or []) if _score(r.get("relevance_score")) >= 6)
    contact_ct  = len(contacts) if contacts else 0
    li_ct       = sum(1 for c in (contacts or []) if c.get("linkedin_url", "").strip())
    draft_ct    = len(drafts) if drafts else 0

    # angle distribution
    angles = {}
    for r in (csr or []):
        angle = angle_from_summary(r.get("analysis_summary", ""))
        angles[angle] = angles.get(angle, 0) + 1
    max_angle = max(angles.values(), default=1)

    # --- Build HTML ---
    html = '<h1>Pipeline Overview</h1>'

    # stat cards
    html += '<div class="cards">'
    html += _card("Companies", total, "in pipeline")
    html += _card("Scanned", csr_found, f"{total - csr_found} not scanned" if total else "")
    html += _card("Relevant", relevant, "score ≥ 6")
    html += _card("Contacts", contact_ct, f"{li_ct} with LinkedIn")
    html += _card("Drafts", draft_ct, "emails generated")
    html += '</div>'

    # angle bar chart
    if angles:
        html += '<h2>Best Angle Distribution</h2>'
        html += '<div class="bar-chart">'
        for angle, count in sorted(angles.items(), key=lambda x: -x[1]):
            pct = int(count / max_angle * 100)
            color = ANGLE_COLORS.get(angle, "#2d6a4f")
            html += (
                f'<div class="bar-row">'
                f'<span class="bar-label">{angle}</span>'
                f'<div class="bar-track"><div class="bar-fill" style="width:{pct}%;background:{color}"></div></div>'
                f'<span class="bar-count">{count}</span>'
                f'</div>'
            )
        html += '</div>'

    # CSR table
    html += '<h2>CSR Analysis</h2>'
    if not csr:
        html += '<div class="alert">No CSR data found. Run stage 3 of the pipeline.</div>'
    else:
        html += '<div class="table-wrap"><table>'
        html += (
            '<tr>'
            '<th>Company</th>'
            '<th>Score</th>'
            '<th>Best Angle</th>'
            '<th>Plastic</th>'
            '<th>Education</th>'
            '<th>Sustainability</th>'
            '<th>Key Quote</th>'
            '<th></th>'
            '</tr>'
        )
        for r in csr:
            name   = r.get("company_name", "")
            score  = r.get("relevance_score", "")
            angle  = angle_from_summary(r.get("analysis_summary", ""))
            color  = ANGLE_COLORS.get(angle, "#2d6a4f")
            sc     = _score(score)
            sc_col = score_color(score)
            plastic = bool_icon(r.get("mentions_plastic_waste", ""))
            edu     = bool_icon(r.get("mentions_education", ""))
            sust    = bool_icon(r.get("mentions_sustainability", ""))
            quote   = truncate(r.get("key_quotes", ""), 80)
            slug    = quote_company(name)

            plastic_cls = "icon-check" if plastic == "✓" else "icon-dash"
            edu_cls     = "icon-check" if edu == "✓" else "icon-dash"
            sust_cls    = "icon-check" if sust == "✓" else "icon-dash"

            html += (
                f'<tr>'
                f'<td><strong>{_esc(name)}</strong></td>'
                f'<td><span class="score-pill" style="background:{sc_col}">{_esc(str(score))}</span></td>'
                f'<td><span class="badge" style="background:{color}">{angle}</span></td>'
                f'<td class="{plastic_cls}">{plastic}</td>'
                f'<td class="{edu_cls}">{edu}</td>'
                f'<td class="{sust_cls}">{sust}</td>'
                f'<td style="color:#555;font-style:italic">{_esc(quote)}</td>'
                f'<td><a href="/company/{slug}">View →</a></td>'
                f'</tr>'
            )
        html += '</table></div>'

    return render_page("Overview", "home", html)


# ---------------------------------------------------------------------------
# Company Detail
# ---------------------------------------------------------------------------

@app.route("/company/<path:company_slug>")
def company_detail(company_slug):
    name = unquote(company_slug)

    companies = read_csv("companies.csv") or []
    csr       = read_csv("csr_analysis.csv") or []
    contacts  = read_csv("contacts.csv") or []
    drafts    = read_csv("outreach_drafts.csv") or []

    co   = next((r for r in companies if r.get("company_name") == name), {})
    csr_r = next((r for r in csr if r.get("company_name") == name), {})
    ct   = next((r for r in contacts if r.get("company_name") == name), {})
    dr   = next((r for r in drafts if r.get("company_name") == name), {})

    if not co and not csr_r:
        abort(404)

    score     = csr_r.get("relevance_score", "N/A")
    angle     = angle_from_summary(csr_r.get("analysis_summary", ""))
    angle_col = ANGLE_COLORS.get(angle, "#2d6a4f")
    sc_col    = score_color(score)

    li_url    = ct.get("linkedin_url", "").strip()
    li_link   = f'<a href="{_esc(li_url)}" target="_blank">{_esc(li_url)}</a>' if li_url else "—"

    email_body  = dr.get("email_body", "").replace("\\n", "\n")
    li_note     = dr.get("linkedin_note", "")
    li_note_len = len(li_note)

    # Attempt to pull linkedin follow-up from apollo export if available
    apollo = read_csv("export_apollo.csv") or []
    apollo_r = next((r for r in apollo if r.get("Company") == name), {})
    li_followup = apollo_r.get("LinkedIn Note", "")  # fallback empty

    html = f'<a class="back-link" href="/">← Back to Overview</a>'
    html += f'<h1>{_esc(name)}</h1>'

    # Company info
    html += '<div class="section-box">'
    html += '<h2>Company Info</h2>'
    html += '<div class="info-grid">'
    html += _info("Industry", co.get("industry", "—"))
    html += _info("Size", co.get("size_estimate", "—"))
    html += _info("Country", co.get("country", "—"))
    html += _info("Website", co.get("website", "—"))
    html += '</div></div>'

    # CSR Analysis
    html += '<div class="section-box">'
    html += '<h2>CSR Analysis</h2>'
    html += '<div class="info-grid">'
    html += _info("Score", f'<span class="score-pill" style="background:{sc_col}">{_esc(str(score))}</span>')
    html += _info("Best Angle", f'<span class="badge" style="background:{angle_col}">{angle}</span>')
    html += _info("Plastic Waste", bool_icon(csr_r.get("mentions_plastic_waste", "")))
    html += _info("Education", bool_icon(csr_r.get("mentions_education", "")))
    html += _info("Sustainability", bool_icon(csr_r.get("mentions_sustainability", "")))
    csr_url = csr_r.get("csr_url", "").strip()
    csr_link = f'<a href="{_esc(csr_url)}" target="_blank">View CSR page →</a>' if csr_url else "—"
    html += _info("CSR URL", csr_link)
    html += '</div>'
    html += '<div class="field-label">Analysis Summary</div>'
    html += f'<textarea rows="4" readonly>{_esc(csr_r.get("analysis_summary", ""))}</textarea>'
    html += '<div class="field-label">Key Quotes</div>'
    html += f'<textarea rows="3" readonly>{_esc(csr_r.get("key_quotes", ""))}</textarea>'
    html += '</div>'

    # Contact info
    html += '<div class="section-box">'
    html += '<h2>Contact</h2>'
    html += '<div class="info-grid">'
    html += _info("Name", ct.get("contact_name", "—"))
    html += _info("Title", ct.get("contact_title", "—"))
    html += _info("Email", ct.get("contact_email", "—"))
    html += _info("Email Confidence", ct.get("email_confidence", "—"))
    html += _info("LinkedIn", li_link)
    html += _info("Source", ct.get("contact_source", "—"))
    html += '</div></div>'

    # Outreach drafts
    html += '<div class="section-box">'
    html += '<h2>Outreach Drafts</h2>'
    html += f'<div class="field-label">Email Subject</div>'
    html += f'<p style="font-weight:600;margin-bottom:12px">{_esc(dr.get("email_subject","—"))}</p>'
    html += '<div class="field-label">Email Body</div>'
    html += f'<textarea rows="12">{_esc(email_body)}</textarea>'
    html += '<div class="field-label">LinkedIn Connection Note</div>'
    html += f'<textarea rows="3" id="li_note">{_esc(li_note)}</textarea>'
    html += f'<div class="char-count">{li_note_len} characters</div>'
    if li_followup:
        html += '<div class="field-label">LinkedIn Follow-up Message</div>'
        html += f'<textarea rows="4">{_esc(li_followup)}</textarea>'
    html += '</div>'

    # Status
    html += '<div class="section-box">'
    html += '<h2>Status</h2>'
    html += (
        '<select>'
        '<option>Not contacted</option>'
        '<option>Connection sent</option>'
        '<option>Connection accepted</option>'
        '<option>Email sent</option>'
        '<option>Replied</option>'
        '</select>'
        '<p style="font-size:12px;color:#888;margin-top:8px">Visual only — not persisted.</p>'
    )
    html += '</div>'

    return render_page(name, "home", html)


# ---------------------------------------------------------------------------
# LinkedIn Queue
# ---------------------------------------------------------------------------

@app.route("/linkedin")
def linkedin_queue():
    rows = read_csv("export_linkedin_queue.csv")

    html = '<h1>LinkedIn Queue</h1>'

    if rows is None:
        html += '<div class="alert">No LinkedIn queue file found (<code>data/export_linkedin_queue.csv</code>). Run the pipeline to generate it.</div>'
        # Fallback: show contacts with LinkedIn URLs from contacts.csv
        contacts = read_csv("contacts.csv") or []
        li_contacts = [c for c in contacts if c.get("linkedin_url", "").strip()]
        if li_contacts:
            html += '<h2>Contacts with LinkedIn URLs (from contacts.csv)</h2>'
            html += '<div class="day-group">'
            html += '<div class="day-header">All contacts</div>'
            for i, c in enumerate(li_contacts, 1):
                drafts = read_csv("outreach_drafts.csv") or []
                dr = next((d for d in drafts if d.get("company_name") == c.get("company_name")), {})
                note = dr.get("linkedin_note", "")
                li_url = c.get("linkedin_url", "").strip()
                html += '<div class="queue-item">'
                html += f'<div class="queue-meta">#{i} · {_esc(c.get("company_name",""))}</div>'
                html += f'<div class="queue-name">{_esc(c.get("contact_name",""))} <span style="font-weight:400;color:#636e72">· {_esc(c.get("contact_title",""))}</span></div>'
                if li_url:
                    html += f'<div style="margin:6px 0;font-size:13px"><a href="{_esc(li_url)}" target="_blank">{_esc(li_url)}</a></div>'
                if note:
                    html += '<div class="field-label" style="margin-top:10px">Connection Note</div>'
                    html += f'<textarea rows="2">{_esc(note)}</textarea>'
                    html += f'<div class="char-count">{len(note)} characters</div>'
                html += '</div>'
            html += '</div>'
        return render_page("LinkedIn Queue", "linkedin", html)

    # Group by day
    days = {}
    for row in rows:
        day = row.get("day", row.get("Day", "1"))
        days.setdefault(day, []).append(row)

    for day_num in sorted(days.keys(), key=lambda x: int(x) if str(x).isdigit() else 0):
        html += f'<div class="day-group">'
        html += f'<div class="day-header">Day {_esc(str(day_num))}</div>'
        group = sorted(days[day_num], key=lambda r: int(r.get("priority", r.get("Priority", 999))))
        for row in group:
            priority = row.get("priority", row.get("Priority", ""))
            name     = row.get("name", row.get("Name", row.get("contact_name", "")))
            title    = row.get("title", row.get("Title", row.get("contact_title", "")))
            company  = row.get("company", row.get("Company", row.get("company_name", "")))
            note     = row.get("connection_note", row.get("linkedin_note", ""))
            status   = row.get("status", row.get("Status", ""))
            li_url   = row.get("linkedin_url", row.get("LinkedIn URL", ""))

            html += '<div class="queue-item">'
            html += f'<div class="queue-meta">Priority #{_esc(str(priority))} · {_esc(company)}'
            if status:
                html += f' · <em>{_esc(status)}</em>'
            html += '</div>'
            html += f'<div class="queue-name">{_esc(name)} <span style="font-weight:400;color:#636e72">· {_esc(title)}</span></div>'
            if li_url:
                html += f'<div style="margin:6px 0;font-size:13px"><a href="{_esc(li_url)}" target="_blank">{_esc(li_url)}</a></div>'
            if note:
                html += '<div class="field-label" style="margin-top:10px">Connection Note</div>'
                html += f'<textarea rows="2">{_esc(note)}</textarea>'
                html += f'<div class="char-count">{len(note)} characters</div>'
            html += (
                '<div class="field-label" style="margin-top:10px">Status</div>'
                '<select>'
                '<option>Not sent</option>'
                '<option>Sent</option>'
                '<option>Accepted</option>'
                '<option>Replied</option>'
                '</select>'
            )
            html += '</div>'
        html += '</div>'

    return render_page("LinkedIn Queue", "linkedin", html)


# ---------------------------------------------------------------------------
# Export
# ---------------------------------------------------------------------------

@app.route("/export")
def export_page():
    exports = [
        ("export_apollo.csv",        "Apollo CSV",        "For Apollo.io import"),
        ("export_lemlist.csv",       "Lemlist CSV",       "For Lemlist email sequences"),
        ("export_linkedin_queue.csv","LinkedIn Queue CSV","Prioritized LinkedIn outreach"),
    ]

    html = '<h1>Export Files</h1>'

    for filename, label, desc in exports:
        size_str, row_count = file_info(filename)
        html += '<div class="export-card">'
        html += f'<div class="export-info"><div class="name">{label}</div>'
        if size_str:
            html += f'<div class="meta">{filename} · {size_str} · {row_count} rows</div>'
        else:
            html += f'<div class="meta" style="color:#e17055">{filename} — not generated yet</div>'
        html += f'<div class="meta" style="margin-top:2px;color:#636e72">{desc}</div>'
        html += '</div>'
        if size_str:
            html += f'<a class="btn" href="/download/{filename}" download>Download ↓</a>'
        else:
            html += '<span class="btn btn-disabled">Not available</span>'
        html += '</div>'

    html += '<p style="font-size:13px;color:#888;margin-top:24px">Files are read live from <code>data/</code>. Re-run the pipeline to refresh.</p>'

    return render_page("Export", "export", html)


@app.route("/download/<filename>")
def download_file(filename):
    # Only allow known CSV filenames for safety
    allowed = {
        "export_apollo.csv",
        "export_lemlist.csv",
        "export_linkedin_queue.csv",
        "csr_analysis.csv",
        "contacts.csv",
        "outreach_drafts.csv",
        "companies.csv",
    }
    if filename not in allowed:
        abort(404)
    path = data_path(filename)
    if not os.path.exists(path):
        abort(404)
    from flask import send_file
    return send_file(path, as_attachment=True, download_name=filename)


# ---------------------------------------------------------------------------
# Utility functions
# ---------------------------------------------------------------------------

def _score(val):
    try:
        return int(float(val))
    except (ValueError, TypeError):
        return 0


def _esc(text):
    """HTML-escape a string."""
    if text is None:
        return ""
    return (
        str(text)
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


def _card(label, value, sub=""):
    return (
        f'<div class="card">'
        f'<div class="label">{label}</div>'
        f'<div class="value">{value}</div>'
        f'{"<div class=sub>" + _esc(str(sub)) + "</div>" if sub else ""}'
        f'</div>'
    )


def _info(key, val):
    return (
        f'<div class="info-item">'
        f'<div class="key">{_esc(key)}</div>'
        f'<div class="val">{val}</div>'
        f'</div>'
    )


def quote_company(name):
    return quote(name, safe="")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    print("Outreach Dashboard running at http://localhost:5001")
    app.run(debug=True, port=5001, host="0.0.0.0")
