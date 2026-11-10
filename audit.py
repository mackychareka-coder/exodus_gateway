"""Audit-log writer. Uses its own transaction so a denial is recorded even when the request rolls back."""
import json

from flask import current_app, g, has_request_context, request

import db


def log_event(action, outcome="success", *, target_type=None, target_id=None, detail=None,
              user_id=None, terminal_id=None):
    """Never raises: auditing must not take the gate down. Never pass secrets in `detail`."""
    try:
        uid = user_id if user_id is not None else g.get("user_id")
        tid = terminal_id if terminal_id is not None else g.get("terminal_id")
        role = g.get("user_role")
        ip = request.remote_addr if has_request_context() else None
        ua = (request.headers.get("User-Agent") or "")[:255] if has_request_context() else None
        with db.get_cursor() as cur:
            cur.execute(
                """INSERT INTO audit_logs (actor_user_id, actor_role, terminal_id, action, outcome,
                                           target_type, target_id, ip_address, user_agent, detail)
                   VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                (uid, role, tid, action, outcome, target_type,
                 None if target_id is None else str(target_id)[:64], ip, ua,
                 json.dumps(detail, default=str) if detail else None),
            )
    except Exception:  # noqa: BLE001
        current_app.logger.exception("audit write failed for %s", action)
