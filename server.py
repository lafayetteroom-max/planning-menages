#!/usr/bin/env python3
"""
Planning Ménages — Serveur Flask

Sert le dashboard, proxy Lodgify API (CORS), et stocke l'état partagé
(tâches cochées, notes, horaires, compteurs invités) dans un fichier JSON.

Usage local :
    pip install flask
    python3 server.py
    → http://localhost:3000

Déploiement Render :
    Voir render.yaml + requirements.txt
"""

import json
import os
import threading
import time
import requests as ext_requests

from flask import Flask, request, jsonify, send_file, Response

app = Flask(__name__)

PORT = int(os.environ.get("PORT", 3000))
LODGIFY_BASE = "https://api.lodgify.com"
DIR = os.path.dirname(os.path.abspath(__file__))
STATE_FILE = os.path.join(DIR, "shared_state.json")

# Thread lock for safe concurrent state writes
state_lock = threading.Lock()


def load_state():
    """Load shared state from JSON file."""
    try:
        with open(STATE_FILE, "r") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {
            "cleaningStatus": {},
            "cleaningNotes": {},
            "checklistState": {},
            "customTimes": {},
            "customGuests": {},
            "version": 0,
        }


def save_state(state):
    """Save shared state to JSON file."""
    with state_lock:
        state["version"] = state.get("version", 0) + 1
        with open(STATE_FILE, "w") as f:
            json.dump(state, f, ensure_ascii=False)


# ===== ROUTES =====

@app.route("/")
@app.route("/index.html")
def serve_dashboard():
    html_path = os.path.join(DIR, "planning-menages-app.html")
    return send_file(html_path, mimetype="text/html")


@app.route("/state", methods=["GET"])
def get_state():
    """Return shared state (for sync between users)."""
    state = load_state()
    resp = jsonify(state)
    resp.headers["Cache-Control"] = "no-cache"
    return resp


@app.route("/state", methods=["POST"])
def update_state():
    """Merge incoming state changes into shared state."""
    incoming = request.get_json(force=True)
    with state_lock:
        state = load_state()
        # Merge each section (incoming overwrites keys, doesn't delete missing ones)
        for key in ["cleaningStatus", "cleaningNotes", "checklistState",
                     "customTimes", "customGuests"]:
            if key in incoming:
                if not isinstance(state.get(key), dict):
                    state[key] = {}
                state[key].update(incoming[key])
        save_state(state)
    return jsonify({"ok": True, "version": state["version"]})


@app.route("/state/version", methods=["GET"])
def get_version():
    """Quick check: has state changed since last sync?"""
    state = load_state()
    return jsonify({"version": state.get("version", 0)})


# ===== LODGIFY PROXY (bypass CORS) =====

@app.route("/api/<path:lodgify_path>", methods=["GET", "POST", "OPTIONS"])
def proxy_lodgify(lodgify_path):
    if request.method == "OPTIONS":
        resp = Response("", status=204)
        resp.headers["Access-Control-Allow-Origin"] = "*"
        resp.headers["Access-Control-Allow-Methods"] = "GET, POST, OPTIONS"
        resp.headers["Access-Control-Allow-Headers"] = "Content-Type, X-ApiKey"
        return resp

    lodgify_url = f"{LODGIFY_BASE}/{lodgify_path}"
    if request.query_string:
        lodgify_url += f"?{request.query_string.decode()}"

    api_key = request.headers.get("X-ApiKey", "")
    headers = {
        "X-ApiKey": api_key,
        "Accept": "application/json",
        "Content-Type": "application/json",
        "User-Agent": "PlanningMenages/1.0",
    }

    try:
        if request.method == "POST":
            ext_resp = ext_requests.post(lodgify_url, headers=headers,
                                         data=request.get_data(), timeout=15)
        else:
            ext_resp = ext_requests.get(lodgify_url, headers=headers, timeout=15)

        resp = Response(ext_resp.content, status=ext_resp.status_code)
        resp.headers["Content-Type"] = ext_resp.headers.get("Content-Type", "application/json")
        resp.headers["Access-Control-Allow-Origin"] = "*"
        return resp

    except Exception as e:
        resp = jsonify({"error": f"Connexion Lodgify échouée: {str(e)}"})
        resp.status_code = 502
        resp.headers["Access-Control-Allow-Origin"] = "*"
        return resp


# ===== MAIN =====

if __name__ == "__main__":
    print()
    print("  🏠 Planning Ménages")
    print("  " + "─" * 36)
    print(f"  ✅ Serveur démarré sur http://localhost:{PORT}")
    print("  📋 Ouvrez ce lien dans votre navigateur")
    print("  📱 Partagez ce lien avec l'équipe de ménage")
    print("  ⏹  Ctrl+C pour arrêter")
    print()

    # For local use: open browser
    import webbrowser, subprocess, sys
    def open_delayed():
        time.sleep(1)
        try:
            if sys.platform == "darwin":
                subprocess.run(["open", f"http://localhost:{PORT}"], check=False)
            else:
                webbrowser.open(f"http://localhost:{PORT}")
        except:
            pass
    threading.Thread(target=open_delayed, daemon=True).start()

    app.run(host="0.0.0.0", port=PORT, debug=False)
