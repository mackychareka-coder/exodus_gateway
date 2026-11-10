"""Pluggable host notification: log (default), SMTP email, or signed webhook.

Emails are sent in a background thread so a slow mail server never delays the gate. Every attempt is
written to the audit log (notify.sent / notify.failed) so admins can see why an email did not arrive.
"""
import hashlib
import hmac
import html
import json
import logging
import smtplib
import threading
import urllib.request
from email.message import EmailMessage

from flask import current_app

import audit

log = logging.getLogger("exodus.notifier")


def _t(v):
    """Free text is stored HTML-escaped; plain-text channels need the real characters."""
    return "" if v is None else html.unescape(str(v))


def render(event, ctx):
    name, purpose, host = _t(ctx.get("visitor_name")), _t(ctx.get("purpose")), _t(ctx.get("host_name"))
    if event == "visitor.approval_requested":
        subject = f"Approval needed: {name} is at reception"
        lead = f"{name} is at reception asking to see you and is waiting for approval."
        tail = "Please let reception know whether to admit this visitor.\n"
    else:
        subject = f"Visitor at the gate: {name}"
        lead = f"{name} is at the gate to see you."
        tail = ""
    body = (f"Hello {host},\n\n{lead}\n\nVisitor: {name}\nPurpose of visit: {purpose}\n"
            f"Badge code: {ctx.get('badge_code')}\n\n{tail}- EXODUS & CO Reception\n")
    return " ".join(subject.split()), body


class LogNotifier:
    def __init__(self, cfg): pass

    def send(self, event, ctx):
        subject, body = render(event, ctx)
        log.info("NOTIFY -> %s <%s>: %s | purpose=%s", ctx.get("host_name"), ctx.get("host_email"), subject,
                 _t(ctx.get("purpose")))


class SmtpNotifier:
    def __init__(self, cfg): self.cfg = cfg

    def send(self, event, ctx):
        c = self.cfg
        subject, body = render(event, ctx)
        msg = EmailMessage()
        msg["From"], msg["To"], msg["Subject"] = c["SMTP_FROM"], ctx["host_email"], subject
        msg.set_content(body)
        smtp_cls = smtplib.SMTP_SSL if c["SMTP_SSL"] else smtplib.SMTP
        with smtp_cls(c["SMTP_HOST"], c["SMTP_PORT"], timeout=10) as s:
            if c["SMTP_STARTTLS"] and not c["SMTP_SSL"]:
                s.starttls()
            if c["SMTP_USER"]:
                s.login(c["SMTP_USER"], c["SMTP_PASSWORD"])
            s.send_message(msg)


class WebhookNotifier:
    def __init__(self, cfg): self.cfg = cfg

    def send(self, event, ctx):
        payload = json.dumps({"event": event, **ctx}, default=str).encode()
        req = urllib.request.Request(self.cfg["WEBHOOK_URL"], data=payload, method="POST",
                                     headers={"Content-Type": "application/json"})
        if self.cfg["WEBHOOK_SECRET"]:
            req.add_header("X-Exodus-Signature",
                           hmac.new(self.cfg["WEBHOOK_SECRET"].encode(), payload, hashlib.sha256).hexdigest())
        urllib.request.urlopen(req, timeout=5).close()


BACKENDS = {"log": LogNotifier, "smtp": SmtpNotifier, "webhook": WebhookNotifier}


def _deliver(app, cfg, event, ctx):
    backend = cfg["NOTIFIER_BACKEND"]
    detail = {"event": event, "backend": backend, "host": ctx.get("host_code")}
    try:
        if backend == "smtp" and not ctx.get("host_email"):
            ok, detail["reason"] = False, "host has no email address on file"
        else:
            BACKENDS[backend](cfg).send(event, ctx)
            ok = True
    except Exception as e:  # noqa: BLE001
        log.exception("notification %s failed", event)
        ok, detail["reason"] = False, f"{type(e).__name__}: {e}"[:200]
    with app.app_context():
        audit.log_event("notify.sent" if ok else "notify.failed", "success" if ok else "error",
                        target_type="visit", target_id=ctx.get("visit_id"), detail=detail)


def notify(event, visit):
    """Called after the DB transaction commits. Never raises."""
    try:
        app = current_app._get_current_object()
        ctx = {k: visit.get(k) for k in ("visitor_name", "purpose", "badge_code", "status",
                                         "host_name", "host_email", "host_code")}
        ctx["visit_id"] = visit.get("id")
        cfg = dict(app.config)
        if cfg.get("TESTING") or not cfg["NOTIFY_ASYNC"]:
            _deliver(app, cfg, event, ctx)
        else:
            threading.Thread(target=_deliver, args=(app, cfg, event, ctx), daemon=True).start()
    except Exception:  # noqa: BLE001
        log.exception("could not queue notification %s", event)
