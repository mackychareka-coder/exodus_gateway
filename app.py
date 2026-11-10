"""Application factory + entry point.  Production:  gunicorn -c gunicorn.conf.py "app:create_app()" """
from datetime import date, datetime

from flask import Flask, request
from flask.json.provider import DefaultJSONProvider
from werkzeug.middleware.proxy_fix import ProxyFix

import auth
import db
from cli import register_cli
from config import load_config
from errors import ok, register_error_handlers
from extensions import init_extensions, limiter
from scheduler import start_scheduler


class _JSON(DefaultJSONProvider):
    sort_keys = False

    @staticmethod
    def default(o):
        if isinstance(o, (datetime, date)):
            return o.isoformat()
        return DefaultJSONProvider.default(o)


def create_app(overrides=None):
    app = Flask(__name__)
    app.config.update(load_config())
    app.config.update(overrides or {})
    app.json = _JSON(app)

    if app.config["TRUSTED_PROXIES"]:
        n = app.config["TRUSTED_PROXIES"]
        app.wsgi_app = ProxyFix(app.wsgi_app, x_for=n, x_proto=n, x_host=n)
    if app.config["APP_ENV"] == "production" and app.config["RATELIMIT_STORAGE_URI"].startswith("memory://"):
        app.logger.warning("Rate limiter uses in-memory storage; use redis:// with multiple workers.")

    db.init_db(app)
    init_extensions(app)                   # limiter first ...
    app.before_request(auth.authenticate)  # ... then authentication

    from routes import admin, auth_routes, checkin, pages, visitors
    for module in (auth_routes, checkin, visitors, admin, pages):
        app.register_blueprint(module.bp)

    register_error_handlers(app)
    register_cli(app)
    start_scheduler(app)

    @app.get("/health")
    @limiter.exempt
    def health():
        return ok({"service": "exodus_gateway"})

    @app.after_request
    def _no_store(resp):
        if request.path.startswith("/api/"):
            resp.headers["Cache-Control"] = "no-store"
        return resp

    return app


if __name__ == "__main__":
    create_app().run(host="127.0.0.1", port=5000, debug=False)  # dev only; never debug=True
