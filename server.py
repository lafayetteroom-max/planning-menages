#!/usr/bin/env python3
"""
Planning Ménages — Serveur Flask

Sert le dashboard, proxy Lodgify API (CORS), et stocke l'état partagé.
Cache les réservations Lodgify en mémoire avec rafraîchissement en arrière-plan.
"""

import json
import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor
import requests as ext_requests

from flask import Flask, request, jsonify, send_file, Response

app = Flask(__name__)

PORT = int(os.environ.get("PORT", 3000))
LODGIFY_BASE = "https://api.lodgify.com"
LODGIFY_API_KEY = "wE+20De2+LfCRgWrBnqWm55IrW/Xkae0rzmTkHYAEUasoIxxJAA+puWgyr3XO0MA"
DIR = os.path.dirname(os.path.abspath(__file__))
STATE_FILE = os.path.join(DIR, "shared_state.json")

# Thread lock for safe concurrent state writes
state_lock = threading.Lock()

# ===== LODGIFY CACHE =====
cache_lock = threading.Lock()
lodgify_cache = {
    "properties": None,
    "reservations": None,
    "last_refresh": 0,
    "refreshing": False,
}
CACHE_TTL = 5 * 60  # Refresh every 5 minutes


def _fetch_page(offset, page_size=50):
    """Fetch one page of reservations from Lodgify."""
    url = f"{LODGIFY_BASE}/v1/reservation?offset={offset}&limit={page_size}"
    headers = {
        "X-ApiKey": LODGIFY_API_KEY,
        "Accept": "application/json",
        "User-Agent": "PlanningMenages/1.0",
    }
    try:
        r = ext_requests.get(url, headers=headers, timeout=30)
        if r.ok:
            data = r.json()
            return data if isinstance(data, list) else []
        return []
    except Exception:
        return []


def _refresh_cache():
    """Fetch all properties + reservations from Lodgify and update cache."""
    with cache_lock:
        if lodgify_cache["refreshing"]:
            return
        lodgify_cache["refreshing"] = True

    try:
        headers = {
            "X-ApiKey": LODGIFY_API_KEY,
            "Accept": "application/json",
            "User-Agent": "PlanningMenages/1.0",
        }

        # Fetch properties
        try:
            r = ext_requests.get(f"{LODGIFY_BASE}/v2/properties", headers=headers, timeout=30)
            properties = r.json() if r.ok else []
        except Exception:
            properties = []

        # Fetch first page to know total
        first_page = _fetch_page(0)
        all_reservations = list(first_page)

        # Fetch remaining pages in parallel
        if len(first_page) >= 50:
            offsets = list(range(50, 1200, 50))
            with ThreadPoolExecutor(max_workers=10) as executor:
                pages = list(executor.map(_fetch_page, offsets))
            for page in pages:
                if not page:
                    break
                all_reservations.extend(page)

        with cache_lock:
            lodgify_cache["properties"] = properties
            lodgify_cache["reservations"] = all_reservations
            lodgify_cache["last_refresh"] = time.time()
            lodgify_cache["refreshing"] = False

        print(f"Cache refreshed: {len(properties)} properties, {len(all_reservations)} reservations")

    except Exception as e:
        print(f"Cache refresh error: {e}")
        with cache_lock:
            lodgify_cache["refreshing"] = False


def _ensure_cache():
    """Start background refresh if cache is stale."""
    age = time.time() - lodgify_cache["last_refresh"]
    if age > CACHE_TTL or lodgify_cache["reservations"] is None:
        threading.Thread(target=_refresh_cache, daemon=True).start()


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


# ===== CACHED BULK ENDPOINT =====

@app.route("/api/bulk", methods=["GET"])
def bulk_data():
    """Return all properties + reservations from cache (instant response)."""
    _ensure_cache()

    # If cache is empty (first request ever), wait for it
    if lodgify_cache["reservations"] is None:
        for _ in range(60):  # Wait up to 30s
            time.sleep(0.5)
            if lodgify_cache["reservations"] is not None:
                break

    with cache_lock:
        data = {
            "properties": lodgify_cache["properties"] or [],
            "reservations": lodgify_cache["reservations"] or [],
            "cache_age": int(time.time() - lodgify_cache["last_refresh"]),
        }

    resp = jsonify(data)
    resp.headers["Cache-Control"] = "no-cache"
    resp.headers["Access-Control-Allow-Origin"] = "*"
    return resp


# ===== LODGIFY PROXY (bypass CORS) — kept for message scan =====

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
                                         data=request.get_data(), timeout=60)
        else:
            ext_resp = ext_requests.get(lodgify_url, headers=headers, timeout=60)

        resp = Response(ext_resp.content, status=ext_resp.status_code)
        resp.headers["Content-Type"] = ext_resp.headers.get("Content-Type", "application/json")
        resp.headers["Access-Control-Allow-Origin"] = "*"
        return resp

    except Exception as e:
        resp = jsonify({"error": f"Connexion Lodgify échouée: {str(e)}"})
        resp.status_code = 502
        resp.headers["Access-Control-Allow-Origin"] = "*"
        return resp


# ===== STARTUP =====

# Pre-fill cache on startup
threading.Thread(target=_refresh_cache, daemon=True).start()


# ===== MAIN =====

if __name__ == "__main__":
    print()
    print("  Planning Menages")
    print("  " + "-" * 36)
    print(f"  Serveur demarre sur http://localhost:{PORT}")
    print()

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
