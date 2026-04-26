"""
Nudge UI
--------
A local browser popup that asks for new inputs after each pipeline run.

Run on your laptop:
  python nudge_ui.py

What it does:
  - Opens your browser at localhost:5001 automatically
  - Shows the current priority list from the prompt engine
  - Lets you paste text, links, or insights directly into a form
  - Directs you to the right Google Drive folder for files/images/PDFs
  - Saves your text inputs so the next pipeline run picks them up

Zero cost — runs locally, no hosting, no external services.
Close the terminal window when done.
"""

import os
import sys
import threading
import webbrowser
from pathlib import Path
from datetime import datetime
from flask import Flask, request, redirect, url_for

load_path = Path(__file__).parent
sys.path.insert(0, str(load_path))

BASE_DIR     = Path(__file__).parent
PRIORITY_FILE = BASE_DIR / "output" / "prompts" / "priority.txt"
REQUESTS_FILE = BASE_DIR / "output" / "prompts" / "system_requests.txt"
MANUAL_DIR    = BASE_DIR / "inputs" / "manual"
MANUAL_DIR.mkdir(parents=True, exist_ok=True)
INSIGHTS_FILE = MANUAL_DIR / "insights.txt"

DRIVE_INBOX   = "https://drive.google.com/drive/folders/1tnU4bjFcLVdXx57yARwU__PVVvWoWPD1"

app = Flask(__name__)

# ─────────────────────────────────────────────────────────────
# HELPERS
# ─────────────────────────────────────────────────────────────

def read_priority() -> str:
    if PRIORITY_FILE.exists():
        return PRIORITY_FILE.read_text(encoding="utf-8")
    return "No priority file found yet. Run the pipeline first."


def read_requests() -> str:
    if REQUESTS_FILE.exists():
        return REQUESTS_FILE.read_text(encoding="utf-8")
    return ""


def parse_priority_items(priority_text: str) -> list[dict]:
    """Extract priority items as structured list for the UI."""
    items  = []
    colors = {"HIGH": "#ef4444", "MEDIUM": "#f59e0b", "LOW": "#22c55e"}
    emoji  = {"HIGH": "🔴", "MEDIUM": "🟡", "LOW": "🟢"}

    for line in priority_text.splitlines():
        line = line.strip()
        for level in ("HIGH", "MEDIUM", "LOW"):
            if f"[{level}]" in line:
                label = line.split("]", 1)[-1].strip()
                label = label.replace("🔴", "").replace("🟡", "").replace("🟢", "").strip()
                items.append({
                    "level": level,
                    "label": label,
                    "color": colors[level],
                    "emoji": emoji[level],
                })
                break
    return items


# ─────────────────────────────────────────────────────────────
# HTML TEMPLATE
# ─────────────────────────────────────────────────────────────

PAGE = """<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>Intelligence Engine — Input Needed</title>
  <style>
    * { box-sizing: border-box; margin: 0; padding: 0; }

    body {
      font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
      background: #0f1117;
      color: #e2e8f0;
      min-height: 100vh;
      padding: 32px 16px;
    }

    .container { max-width: 760px; margin: 0 auto; }

    .header {
      border-bottom: 1px solid #1e293b;
      padding-bottom: 20px;
      margin-bottom: 28px;
    }
    .header h1 {
      font-size: 22px;
      font-weight: 600;
      color: #f8fafc;
      letter-spacing: -0.3px;
    }
    .header p {
      font-size: 14px;
      color: #64748b;
      margin-top: 6px;
    }

    /* Priority pills */
    .priority-section { margin-bottom: 28px; }
    .priority-section h2 {
      font-size: 13px;
      font-weight: 600;
      text-transform: uppercase;
      letter-spacing: 0.8px;
      color: #64748b;
      margin-bottom: 12px;
    }
    .priority-item {
      display: flex;
      align-items: center;
      gap: 12px;
      padding: 12px 16px;
      background: #1e293b;
      border-radius: 8px;
      margin-bottom: 8px;
      border-left: 3px solid var(--c);
    }
    .pill {
      font-size: 11px;
      font-weight: 700;
      padding: 3px 8px;
      border-radius: 4px;
      background: var(--c);
      color: #fff;
      white-space: nowrap;
    }
    .priority-label { font-size: 14px; color: #cbd5e1; }
    .nothing { color: #22c55e; font-size: 14px; padding: 12px 0; }

    /* Form */
    .form-section { margin-bottom: 28px; }
    .form-section h2 {
      font-size: 13px;
      font-weight: 600;
      text-transform: uppercase;
      letter-spacing: 0.8px;
      color: #64748b;
      margin-bottom: 6px;
    }
    .form-hint {
      font-size: 13px;
      color: #475569;
      margin-bottom: 12px;
    }
    textarea {
      width: 100%;
      background: #1e293b;
      border: 1px solid #334155;
      border-radius: 8px;
      color: #e2e8f0;
      font-size: 14px;
      padding: 14px;
      resize: vertical;
      min-height: 160px;
      outline: none;
      font-family: inherit;
      line-height: 1.6;
    }
    textarea:focus { border-color: #6366f1; }
    textarea::placeholder { color: #475569; }

    .submit-row {
      display: flex;
      gap: 12px;
      align-items: center;
      margin-top: 14px;
    }
    button[type=submit] {
      background: #6366f1;
      color: #fff;
      border: none;
      border-radius: 8px;
      padding: 12px 28px;
      font-size: 15px;
      font-weight: 600;
      cursor: pointer;
      transition: background 0.15s;
    }
    button[type=submit]:hover { background: #4f46e5; }
    .skip-link {
      font-size: 13px;
      color: #475569;
      text-decoration: none;
    }
    .skip-link:hover { color: #94a3b8; }

    /* Drive section */
    .drive-section { margin-bottom: 28px; }
    .drive-section h2 {
      font-size: 13px;
      font-weight: 600;
      text-transform: uppercase;
      letter-spacing: 0.8px;
      color: #64748b;
      margin-bottom: 12px;
    }
    .drive-card {
      background: #1e293b;
      border-radius: 8px;
      padding: 16px;
      margin-bottom: 8px;
    }
    .drive-card h3 {
      font-size: 14px;
      font-weight: 600;
      color: #e2e8f0;
      margin-bottom: 4px;
    }
    .drive-card p {
      font-size: 13px;
      color: #64748b;
      margin-bottom: 10px;
    }
    .drive-path {
      font-size: 12px;
      background: #0f1117;
      border-radius: 4px;
      padding: 6px 10px;
      color: #94a3b8;
      font-family: monospace;
      margin-bottom: 10px;
    }
    .drive-link {
      display: inline-block;
      font-size: 13px;
      color: #6366f1;
      text-decoration: none;
      font-weight: 500;
    }
    .drive-link:hover { text-decoration: underline; }

    /* Success */
    .success-box {
      background: #052e16;
      border: 1px solid #166534;
      border-radius: 10px;
      padding: 28px;
      text-align: center;
    }
    .success-box h2 { font-size: 20px; color: #22c55e; margin-bottom: 10px; }
    .success-box p { font-size: 14px; color: #86efac; line-height: 1.6; }
    .success-box .close-note {
      margin-top: 20px;
      font-size: 13px;
      color: #4ade80;
    }

    hr { border: none; border-top: 1px solid #1e293b; margin: 24px 0; }
  </style>
</head>
<body>
<div class="container">

  {% if submitted %}
  <!-- ── SUCCESS STATE ── -->
  <div class="success-box">
    <h2>✓ Inputs received</h2>
    <p>
      Your text and links have been saved.<br>
      They will be processed on the next pipeline run (within 2 days).<br><br>
      {% if has_file_nudge %}
      <strong>Don't forget:</strong> Upload any screenshots, PDFs, or WhatsApp exports
      to the Google Drive folder linked below before the next run.
      {% endif %}
    </p>
    <p class="close-note">You can close this window now.</p>
  </div>

  {% if has_file_nudge %}
  <br>
  <div class="drive-card">
    <h3>📁 File Upload Reminder</h3>
    <p>WhatsApp exports, screenshots, and PDFs go here:</p>
    <div class="drive-path">Drive → inputs → whatsapp → personal_inbox</div>
    <a class="drive-link" href="{{ drive_url }}" target="_blank">Open Google Drive →</a>
  </div>
  {% endif %}

  {% else %}
  <!-- ── MAIN STATE ── -->
  <div class="header">
    <h1>Intelligence Engine — Input Check</h1>
    <p>{{ timestamp }} &nbsp;·&nbsp; Takes 2 minutes &nbsp;·&nbsp; Close when done</p>
  </div>

  <!-- Priority list -->
  <div class="priority-section">
    <h2>What the system needs this cycle</h2>
    {% if priority_items %}
      {% for item in priority_items %}
      <div class="priority-item" style="--c: {{ item.color }}">
        <span class="pill" style="background: {{ item.color }}">{{ item.level }}</span>
        <span class="priority-label">{{ item.emoji }} {{ item.label }}</span>
      </div>
      {% endfor %}
    {% else %}
      <p class="nothing">✓ Nothing critical needed this cycle. You're all caught up.</p>
    {% endif %}
  </div>

  <hr>

  <!-- Text / link input -->
  <form method="POST" action="/submit">
  <div class="form-section">
    <h2>Paste anything you've come across</h2>
    <p class="form-hint">
      Links, articles, research, a thought, a competitor post, a clinical insight —
      anything relevant to women's health, your content strategy, or your brands.
      One item per line is easiest, but any format works.
    </p>
    <textarea
      name="insights"
      placeholder="https://example.com/menopause-adhd-study&#10;&#10;Interesting thread I saw — women are saying their ADHD symptoms peak the week before their period, not just during perimenopause. This is a totally underserved angle.&#10;&#10;https://instagram.com/p/..."
    ></textarea>

    <div class="submit-row">
      <button type="submit">Save and finish</button>
      <a class="skip-link" href="/skip">Nothing to add this cycle →</a>
    </div>
  </div>
  </form>

  <hr>

  <!-- Drive upload cards -->
  <div class="drive-section">
    <h2>For files, images, and WhatsApp exports</h2>

    <div class="drive-card">
      <h3>📱 WhatsApp Chat Export</h3>
      <p>Export your personal saved-content group (with media) and upload here:</p>
      <div class="drive-path">Drive → inputs → whatsapp → personal_inbox → chat.txt + images</div>
      <a class="drive-link" href="{{ drive_url }}" target="_blank">Open Google Drive →</a>
    </div>

    <div class="drive-card">
      <h3>📸 Screenshots &amp; Images</h3>
      <p>Research screenshots, Instagram posts, graphs, anything visual:</p>
      <div class="drive-path">Drive → inputs → whatsapp → personal_inbox → *.jpg / *.png</div>
      <a class="drive-link" href="{{ drive_url }}" target="_blank">Open Google Drive →</a>
    </div>

    <div class="drive-card">
      <h3>📄 PDFs &amp; Documents</h3>
      <p>Research papers, reports, clinical guidelines you want Claude to extract from:</p>
      <div class="drive-path">Drive → inputs → whatsapp → personal_inbox → *.pdf</div>
      <a class="drive-link" href="{{ drive_url }}" target="_blank">Open Google Drive →</a>
    </div>
  </div>

  {% endif %}

</div>
</body>
</html>"""


# ─────────────────────────────────────────────────────────────
# ROUTES
# ─────────────────────────────────────────────────────────────

@app.route("/")
def index():
    priority_text  = read_priority()
    priority_items = parse_priority_items(priority_text)
    ts             = datetime.now().strftime("%A, %d %b %Y — %H:%M")
    return render_template_string(
        PAGE,
        submitted      = False,
        priority_items = priority_items,
        timestamp      = ts,
        drive_url      = DRIVE_INBOX,
        has_file_nudge = False,
    )


@app.route("/submit", methods=["POST"])
def submit():
    text = request.form.get("insights", "").strip()

    if text:
        ts_line  = f"\n\n--- Submitted {datetime.now().strftime('%Y-%m-%d %H:%M')} ---\n"
        with open(INSIGHTS_FILE, "a", encoding="utf-8") as f:
            f.write(ts_line + text + "\n")
        print(f"  [UI] {len(text.splitlines())} line(s) saved to {INSIGHTS_FILE}")
    else:
        print("  [UI] No text submitted — form was empty.")

    priority_text  = read_priority()
    priority_items = parse_priority_items(priority_text)

    # Determine if there's a file-upload nudge active
    file_keywords  = ["whatsapp", "personal inbox", "upload", "export"]
    has_file_nudge = any(
        kw in read_requests().lower() for kw in file_keywords
    )

    return render_template_string(
        PAGE,
        submitted      = True,
        priority_items = priority_items,
        timestamp      = datetime.now().strftime("%A, %d %b %Y — %H:%M"),
        drive_url      = DRIVE_INBOX,
        has_file_nudge = has_file_nudge,
    )


@app.route("/skip")
def skip():
    print("  [UI] Skipped — no inputs this cycle.")
    return render_template_string(
        PAGE,
        submitted      = True,
        priority_items = [],
        timestamp      = datetime.now().strftime("%A, %d %b %Y — %H:%M"),
        drive_url      = DRIVE_INBOX,
        has_file_nudge = False,
    )


# ─────────────────────────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────────────────────────

def open_browser():
    import time
    time.sleep(1.2)
    webbrowser.open("http://localhost:5001")


if __name__ == "__main__":
    print("\n" + "=" * 55)
    print("  INTELLIGENCE ENGINE — INPUT NUDGE")
    print("=" * 55)
    print("  Opening in your browser at http://localhost:5001")
    print("  Close this terminal window when you are done.\n")

    threading.Thread(target=open_browser, daemon=True).start()
    app.run(host="127.0.0.1", port=5001, debug=False, use_reloader=False)
