#!/usr/bin/env python3
"""
Planning Ménages — Serveur Flask
Sert le dashboard, proxy Lodgify API (CORS), et stocke l'état partagé.
"""

import json
import os
import threading
import time
from datetime import datetime, timezone, timedelta
import requests as ext_requests

from flask import Flask, request, jsonify, send_file, Response

app = Flask(__name__)

# Horodatage de deploiement (capture au demarrage du serveur = moment du deploy)
_DEPLOY_TIME = datetime.now(timezone(timedelta(hours=2))).strftime("%d/%m/%Y a %Hh%M")

PORT = int(os.environ.get("PORT", 3000))
LODGIFY_BASE = "https://api.lodgify.com"
DIR = os.path.dirname(os.path.abspath(__file__))
STATE_FILE = os.path.join(DIR, "shared_state.json")

state_lock = threading.Lock()


def load_state():
    try:
        with open(STATE_FILE, "r") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {
            "cleaningStatus": {}, "cleaningNotes": {}, "calendarNotes": {},
            "checklistState": {}, "customTimes": {}, "customGuests": {},
            "version": 0,
        }


def save_state(state):
    with state_lock:
        state["version"] = state.get("version", 0) + 1
        with open(STATE_FILE, "w") as f:
            json.dump(state, f, ensure_ascii=False)


@app.route("/")
@app.route("/index.html")
def serve_dashboard():
    html_path = os.path.join(DIR, "planning-menages-app.html")
    resp = send_file(html_path, mimetype="text/html")
    resp.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
    resp.headers["Pragma"] = "no-cache"
    resp.headers["Expires"] = "0"
    return resp


@app.route("/state", methods=["GET"])
def get_state():
    state = load_state()
    resp = jsonify(state)
    resp.headers["Cache-Control"] = "no-cache"
    return resp


@app.route("/state", methods=["POST"])
def update_state():
    incoming = request.get_json(force=True)
    with state_lock:
        state = load_state()
        for key in ["cleaningStatus", "cleaningNotes", "calendarNotes",
                     "checklistState", "customTimes", "customGuests"]:
            if key in incoming:
                if not isinstance(state.get(key), dict):
                    state[key] = {}
                state[key].update(incoming[key])
        save_state(state)
    return jsonify({"ok": True, "version": state["version"]})


@app.route("/state/version", methods=["GET"])
def get_version():
    state = load_state()
    return jsonify({"version": state.get("version", 0)})


@app.route("/deploy-info")
def deploy_info():
    return jsonify({"deployed_at": _DEPLOY_TIME})


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
        resp = jsonify({"error": f"Connexion Lodgify echouee: {str(e)}"})
        resp.status_code = 502
        resp.headers["Access-Control-Allow-Origin"] = "*"
        return resp


if __name__ == "__main__":
    print(f"  Serveur demarre sur http://localhost:{PORT}")
    app.run(host="0.0.0.0", port=PORT, debug=False)
