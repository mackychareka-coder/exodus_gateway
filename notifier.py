"""Pluggable host notification: log (default), SMTP, or signed webhook."""
import hashlib
import hmac
import json
import logging
import smtplib
import urllib.request
from email.message import EmailMessage

from flask import current_app

log = logging.getLogger("exodus.notifier")

SUBJECTS = {
    "visitor.arrived": "Your visitor {visitor_name} has arrived",
    "visitor.approval_requested": "Approval needed: {visitor_name} is at reception",
}


def _subject(event, ctx):
    return SUBJECTS.get(event, event).format(**ctx)


def _body(event, ctx):
    return (f"Visitor: {ctx['visitor_name']}\nPurpose: {ctx['purpose']}\n"
            f"Badge: {ctx['badge_code']}\nStatus: {ctx['status']}\n")


class LogNotifier:
    def __init__(self, cfg): pass

    def send(self, event, ctx):
        log.info("NOTIFY %s -> host=%s (%s): %s", event, ctx["host_name"], ctx["host_email"], _subject(event, ctx))


class SmtpNotifier:
    def __init__(self, cfg): self.cfg = cfg

    def send(self, event, ctx):
        if not ctx.get("host_email"):
            log.warning("Host %s has no email; skipping %s", ctx["host_name"], event)
            return
        c = self.cfg
        msg = EmailMessage()
        msg["From"], msg["To"], msg["Subject"] = c["SMTP_FROM"], ctx["host_email"], _subject(event, ctx)
        msg.set_content(_body(event, ctx))
        with smtplib.SMTP(c["SMTP_HOST"], c["SMTP_PORT"], timeout=5) as s:
            if c["SMTP_STARTTLS"]:
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
            sig = hmac.new(self.cfg["WEBHOOK_SECRET"].encode(), payload, hashlib.sha256).hexdigest()
            req.add_header("X-Exodus-Signature", sig)
        urllib.request.urlopen(req, timeout=5).close()


BACKENDS = {"log": LogNotifier, "smtp": SmtpNotifier, "webhook": WebhookNotifier}


def notify(event, visit):
    """Best-effort, called after the DB transaction commits. Failures are logged, never raised."""
    try:
        cfg = current_app.config
        ctx = {k: visit.get(k) for k in ("visitor_name", "purpose", "badge_code", "status",
                                         "host_name", "host_email")}
        ctx["visit_id"] = visit.get("id")
        BACKENDS[cfg["NOTIFIER_BACKEND"]](cfg).send(event, ctx)
    except Exception:  # noqa: BLE001
        log.exception("notification %s failed", event)
