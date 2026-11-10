"""Consistent JSON error format, with an HTML fallback (errors.html) for page routes."""
from flask import jsonify, render_template, request
from pydantic import ValidationError
from werkzeug.exceptions import HTTPException


class ApiError(Exception):
    def __init__(self, status, code, message, details=None):
        super().__init__(message)
        self.status, self.code, self.message, self.details = status, code, message, details


def error_response(status, code, message, details=None):
    body = {"status": "error", "code": code, "message": message}
    if details:
        body["details"] = details
    resp = jsonify(body)
    resp.status_code = status
    return resp


def ok(data=None, message="OK", status=200):
    body = {"status": "success", "message": message}
    if data is not None:
        body["data"] = data
    resp = jsonify(body)
    resp.status_code = status
    return resp


def _wants_json():
    return request.path.startswith("/api/") or request.path == "/health"


def _render(status, code, title, message, details=None):
    if _wants_json():
        return error_response(status, code, message, details)
    return render_template("errors.html", error_title=title, error_message=message, status=status), status


def register_error_handlers(app):
    @app.errorhandler(ApiError)
    def _api_error(e):
        return _render(e.status, e.code, e.code.replace("_", " ").title(), e.message, e.details)

    @app.errorhandler(ValidationError)
    def _validation(e):
        # Never echo submitted values back (they may be credentials or PII).
        details = [{"field": ".".join(str(p) for p in err["loc"]), "message": err["msg"]} for err in e.errors()]
        return error_response(422, "VALIDATION_ERROR", "Request validation failed.", details)

    @app.errorhandler(HTTPException)
    def _http(e):
        code = (e.name or "error").upper().replace(" ", "_")
        return _render(e.code or 500, code, e.name, e.description)

    @app.errorhandler(Exception)
    def _unhandled(e):
        app.logger.exception("Unhandled error")
        return _render(500, "INTERNAL_ERROR", "Server Error", "An internal error occurred.")
