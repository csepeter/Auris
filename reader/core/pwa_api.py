"""Installable web app: manifest and a service worker for offline listening."""

from __future__ import annotations

import os

from flask import Blueprint, jsonify, send_file

bp = Blueprint("pwa", __name__)
_STATIC = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "static")


@bp.route("/manifest.webmanifest")
def manifest():
    response = jsonify({
        "name": "Auris – magyar hangoskönyvek",
        "short_name": "Auris",
        "lang": "hu",
        "start_url": "/",
        "scope": "/",
        "display": "standalone",
        "background_color": "#1c1a18",
        "theme_color": "#1c1a18",
        "description": "Helyi hangoskönyv-olvasó és -készítő magyar beszédmotorokkal.",
        "icons": [
            {"src": "/static/icon-192.png", "sizes": "192x192", "type": "image/png"},
            {"src": "/static/icon-512.png", "sizes": "512x512", "type": "image/png",
             "purpose": "any maskable"},
            {"src": "/static/favicon.svg", "sizes": "any", "type": "image/svg+xml"},
        ],
    })
    response.mimetype = "application/manifest+json"
    return response


@bp.route("/service-worker.js")
def service_worker():
    # Served from the root so its scope covers every page.
    response = send_file(os.path.join(_STATIC, "js", "service-worker.js"),
                         mimetype="application/javascript")
    response.headers["Cache-Control"] = "no-cache"
    response.headers["Service-Worker-Allowed"] = "/"
    return response
