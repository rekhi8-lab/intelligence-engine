"""
Chatbot Server — Himalayan Naturals FAQ Bot
-------------------------------------------
Flask backend for the website chat widget.
Serves both the /chat API endpoint and the /chatbot.js widget file.

Deploy free on Render.com or Railway.app:
  - Set environment variables: CLAUDE_API_KEY, ALLOWED_ORIGIN
  - Start command: python chatbot_server.py

Local development:
  pip install flask flask-cors
  python chatbot_server.py
  Open chatbot_test.html in a browser to test
"""

import os
import time
from pathlib import Path
from dotenv import load_dotenv
from flask import Flask, request, jsonify, Response
from flask_cors import CORS
import anthropic

load_dotenv(override=True)

# ─────────────────────────────────────────────────────────────
# CONFIG
# ─────────────────────────────────────────────────────────────

BASE_DIR       = Path(__file__).parent
KB_PATH        = BASE_DIR / "knowledge_base.txt"
CLAUDE_API_KEY = os.getenv("CLAUDE_API_KEY")
ALLOWED_ORIGIN = os.getenv("ALLOWED_ORIGIN", "*")   # set to your domain in production
PORT           = int(os.getenv("PORT", 5000))

client = anthropic.Anthropic(api_key=CLAUDE_API_KEY)

app = Flask(__name__)
CORS(app, origins=ALLOWED_ORIGIN)

# ─────────────────────────────────────────────────────────────
# RATE LIMITING — simple in-memory (resets on restart)
# ─────────────────────────────────────────────────────────────

_rate: dict[str, list] = {}

def is_rate_limited(ip: str, max_requests: int = 20, window_seconds: int = 60) -> bool:
    now = time.time()
    _rate.setdefault(ip, [])
    _rate[ip] = [t for t in _rate[ip] if now - t < window_seconds]
    if len(_rate[ip]) >= max_requests:
        return True
    _rate[ip].append(now)
    return False


# ─────────────────────────────────────────────────────────────
# KNOWLEDGE BASE
# ─────────────────────────────────────────────────────────────

def load_knowledge_base() -> str:
    if not KB_PATH.exists():
        return "No knowledge base loaded."
    return KB_PATH.read_text(encoding="utf-8")


# ─────────────────────────────────────────────────────────────
# SYSTEM PROMPT
# ─────────────────────────────────────────────────────────────

SYSTEM_PROMPT_TEMPLATE = """You are a knowledgeable, warm assistant for Himalayan Naturals, a women's health supplement brand founded by Harmanjeet Rekhi.
You help women understand our products and how they relate to hormonal health, menopause, perimenopause, endometriosis, PCOS, and related conditions.

KNOWLEDGE BASE:
{knowledge_base}

RULES:
- Always recommend consulting a doctor before starting any supplement
- Never diagnose medical conditions based on symptoms described
- Never claim products cure, treat, or prevent any medical condition
- If asked about serious or urgent symptoms, direct to a healthcare provider immediately
- Keep responses warm, clear, and under 150 words
- When relevant, mention which product might help — but never push or pressure
- If you don't know something, say so honestly rather than guessing
- Be empathetic — many of our customers have spent years not being believed by the medical system

TONE: Warm, knowledgeable, honest. Like a trusted friend who knows a lot about women's health — not a salesperson."""


def build_system_prompt() -> str:
    kb = load_knowledge_base()
    return SYSTEM_PROMPT_TEMPLATE.format(knowledge_base=kb)


# ─────────────────────────────────────────────────────────────
# CHAT ENDPOINT
# ─────────────────────────────────────────────────────────────

@app.route("/chat", methods=["POST"])
def chat():
    ip = request.headers.get("X-Forwarded-For", request.remote_addr or "unknown").split(",")[0].strip()
    if is_rate_limited(ip):
        return jsonify({"error": "Too many requests. Please wait a moment."}), 429

    data = request.get_json(silent=True)
    if not data:
        return jsonify({"error": "Invalid request."}), 400

    message = (data.get("message") or "").strip()
    history = data.get("history") or []

    if not message:
        return jsonify({"error": "Message is required."}), 400
    if len(message) > 1000:
        return jsonify({"error": "Message too long."}), 400

    # Build message list — history + current message
    messages = []
    for turn in history[-6:]:   # keep last 3 exchanges (6 messages)
        role    = turn.get("role")
        content = turn.get("content", "").strip()
        if role in ("user", "assistant") and content:
            messages.append({"role": role, "content": content})
    messages.append({"role": "user", "content": message})

    try:
        response = client.messages.create(
            model="claude-haiku-4-5-20251001",   # Haiku: fast + cheap for chatbot responses
            max_tokens=300,
            system=build_system_prompt(),
            messages=messages,
        )
        reply = response.content[0].text.strip()
        return jsonify({"reply": reply})

    except Exception as e:
        print(f"[Chat error] {e}")
        return jsonify({"error": "Something went wrong. Please try again."}), 500


# ─────────────────────────────────────────────────────────────
# HEALTH CHECK
# ─────────────────────────────────────────────────────────────

@app.route("/health", methods=["GET"])
def health():
    return jsonify({"status": "ok", "kb_loaded": KB_PATH.exists()})


# ─────────────────────────────────────────────────────────────
# SERVE chatbot.js (so websites only need one <script> tag)
# ─────────────────────────────────────────────────────────────

CHATBOT_JS = r"""
(function () {
  var config = window.HNConfig || {};
  var apiUrl = config.apiUrl || 'http://localhost:5000/chat';
  var accentColor = config.accentColor || '#7c4a85';
  var botName = config.botName || 'Himalayan Naturals';

  var history = [];

  // ── Styles ──────────────────────────────────────────────
  var style = document.createElement('style');
  style.textContent = [
    '#hn-chat-bubble{position:fixed;bottom:24px;right:24px;width:56px;height:56px;border-radius:50%;background:' + accentColor + ';cursor:pointer;display:flex;align-items:center;justify-content:center;box-shadow:0 4px 16px rgba(0,0,0,.25);z-index:9999;transition:transform .2s}',
    '#hn-chat-bubble:hover{transform:scale(1.08)}',
    '#hn-chat-bubble svg{width:28px;height:28px;fill:#fff}',
    '#hn-chat-window{position:fixed;bottom:92px;right:24px;width:340px;max-height:520px;background:#fff;border-radius:16px;box-shadow:0 8px 32px rgba(0,0,0,.18);display:none;flex-direction:column;z-index:9999;overflow:hidden;font-family:-apple-system,BlinkMacSystemFont,Segoe UI,sans-serif;font-size:14px}',
    '#hn-chat-window.open{display:flex}',
    '#hn-chat-header{background:' + accentColor + ';color:#fff;padding:14px 16px;font-weight:600;font-size:15px;display:flex;justify-content:space-between;align-items:center}',
    '#hn-chat-close{background:none;border:none;color:#fff;font-size:20px;cursor:pointer;line-height:1;padding:0}',
    '#hn-chat-messages{flex:1;overflow-y:auto;padding:14px;display:flex;flex-direction:column;gap:10px}',
    '#hn-chat-disclaimer{font-size:11px;color:#999;padding:0 14px 8px;line-height:1.4}',
    '#hn-chat-input-row{display:flex;border-top:1px solid #eee;padding:10px 12px;gap:8px}',
    '#hn-chat-input{flex:1;border:1px solid #ddd;border-radius:20px;padding:8px 14px;font-size:14px;outline:none;resize:none}',
    '#hn-chat-input:focus{border-color:' + accentColor + '}',
    '#hn-chat-send{background:' + accentColor + ';color:#fff;border:none;border-radius:20px;padding:8px 16px;cursor:pointer;font-size:13px;font-weight:600}',
    '#hn-chat-send:disabled{opacity:.5;cursor:not-allowed}',
    '.hn-msg{max-width:82%;padding:9px 13px;border-radius:14px;line-height:1.5}',
    '.hn-msg.user{background:' + accentColor + ';color:#fff;align-self:flex-end;border-bottom-right-radius:4px}',
    '.hn-msg.bot{background:#f3f3f3;color:#222;align-self:flex-start;border-bottom-left-radius:4px}',
    '.hn-msg.typing{color:#999;font-style:italic}'
  ].join('');
  document.head.appendChild(style);

  // ── Bubble ───────────────────────────────────────────────
  var bubble = document.createElement('div');
  bubble.id = 'hn-chat-bubble';
  bubble.innerHTML = '<svg viewBox="0 0 24 24"><path d="M20 2H4c-1.1 0-2 .9-2 2v18l4-4h14c1.1 0 2-.9 2-2V4c0-1.1-.9-2-2-2zm-2 10H6V10h12v2zm0-3H6V7h12v2z"/></svg>';
  document.body.appendChild(bubble);

  // ── Chat window ──────────────────────────────────────────
  var win = document.createElement('div');
  win.id = 'hn-chat-window';
  win.innerHTML =
    '<div id="hn-chat-header">' + botName + ' Assistant<button id="hn-chat-close">&times;</button></div>' +
    '<div id="hn-chat-messages"></div>' +
    '<div id="hn-chat-disclaimer">Always consult your doctor before starting any supplement.</div>' +
    '<div id="hn-chat-input-row">' +
    '<textarea id="hn-chat-input" rows="1" placeholder="Ask about our products..."></textarea>' +
    '<button id="hn-chat-send">Send</button>' +
    '</div>';
  document.body.appendChild(win);

  var msgs   = document.getElementById('hn-chat-messages');
  var input  = document.getElementById('hn-chat-input');
  var send   = document.getElementById('hn-chat-send');
  var close  = document.getElementById('hn-chat-close');

  // ── Open / close ─────────────────────────────────────────
  bubble.addEventListener('click', function () {
    win.classList.toggle('open');
    if (win.classList.contains('open') && msgs.children.length === 0) {
      addMsg('bot', 'Hi! I\'m here to help with questions about Himalayan Naturals products and women\'s hormonal health. What can I help you with today?');
    }
    if (win.classList.contains('open')) input.focus();
  });
  close.addEventListener('click', function () { win.classList.remove('open'); });

  // ── Add message ───────────────────────────────────────────
  function addMsg(role, text) {
    var d = document.createElement('div');
    d.className = 'hn-msg ' + role;
    d.textContent = text;
    msgs.appendChild(d);
    msgs.scrollTop = msgs.scrollHeight;
    return d;
  }

  // ── Send message ──────────────────────────────────────────
  function sendMessage() {
    var text = input.value.trim();
    if (!text) return;
    input.value = '';
    send.disabled = true;

    addMsg('user', text);
    history.push({ role: 'user', content: text });

    var typingEl = addMsg('bot', 'Thinking...');
    typingEl.classList.add('typing');

    fetch(apiUrl, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ message: text, history: history.slice(-6) })
    })
    .then(function (r) { return r.json(); })
    .then(function (data) {
      typingEl.remove();
      var reply = data.reply || data.error || 'Sorry, something went wrong.';
      addMsg('bot', reply);
      history.push({ role: 'assistant', content: reply });
    })
    .catch(function () {
      typingEl.remove();
      addMsg('bot', 'Connection error. Please try again.');
    })
    .finally(function () { send.disabled = false; input.focus(); });
  }

  send.addEventListener('click', sendMessage);
  input.addEventListener('keydown', function (e) {
    if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); sendMessage(); }
  });
})();
"""

@app.route("/chatbot.js")
def serve_chatbot_js():
    return Response(CHATBOT_JS, mimetype="application/javascript")


# ─────────────────────────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────────────────────────

if __name__ == "__main__":
    print(f"\n  Himalayan Naturals Chatbot Server")
    print(f"  Knowledge base: {'loaded' if KB_PATH.exists() else 'NOT FOUND'}")
    print(f"  Listening on:   http://localhost:{PORT}")
    print(f"\n  Embed on your website:")
    print(f'    <script>window.HNConfig = {{ apiUrl: "https://YOUR-DOMAIN/chat" }};</script>')
    print(f'    <script src="https://YOUR-DOMAIN/chatbot.js"></script>')
    print(f"\n  Test locally: open chatbot_test.html in a browser\n")
    app.run(host="0.0.0.0", port=PORT, debug=False)
