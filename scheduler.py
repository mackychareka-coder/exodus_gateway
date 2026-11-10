"""End-of-day automatic check-out.

A background thread wakes every SCHEDULER_INTERVAL_SECONDS and closes every open session whose cutoff
has passed. The rule is idempotent and stateless, so several gunicorn workers can run it safely, and a
restart after downtime catches up on its own:

  * checked in at or before the cutoff time of day  -> closed at that same day's cutoff (e.g. 23:00)
  * checked in after the cutoff                      -> closed at the NEXT day's cutoff
Closed rows are marked (access_logs.check_out_method='AUTO', visits.auto_closed=1) and audited.
"""
import threading

import audit
import db

_CLOSE_AT = ("IF(TIME({col}) <= TIME(%s), TIMESTAMP(DATE({col}), %s), "
             "TIMESTAMP(DATE({col}) + INTERVAL 1 DAY, %s))")


def run_auto_checkout(cutoff):
    """cutoff: 'HH:MM:SS'. Returns (employees_closed, visitors_closed). Needs an app context."""
    c = (cutoff,) * 3
    with db.get_cursor() as cur:
        expr = _CLOSE_AT.format(col="check_in_time")
        cur.execute(f"""UPDATE access_logs SET check_out_time = {expr}, check_out_method = 'AUTO'
                        WHERE check_out_time IS NULL AND {expr} <= NOW()""", c + c)
        employees = cur.rowcount
        expr = _CLOSE_AT.format(col="entry_time")
        cur.execute(f"""UPDATE visits SET status = 'departed', exit_time = {expr}, auto_closed = 1
                        WHERE status = 'on-site' AND entry_time IS NOT NULL AND {expr} <= NOW()""", c + c)
        visitors = cur.rowcount
    if employees or visitors:
        audit.log_event("system.auto_checkout", "success",
                        detail={"employees": employees, "visitors": visitors, "cutoff": cutoff[:5]})
    return employees, visitors


def start_scheduler(app):
    cfg = app.config
    if not cfg["AUTO_CHECKOUT_ENABLED"] or cfg.get("TESTING"):
        return None
    stop = threading.Event()

    def loop():
        stop.wait(10)  # let the app finish starting (and short CLI commands exit) first
        while not stop.is_set():
            try:
                with app.app_context():
                    run_auto_checkout(cfg["AUTO_CHECKOUT_TIME"])
            except Exception:  # noqa: BLE001 - never let the scheduler thread die
                app.logger.exception("auto-checkout run failed")
            stop.wait(cfg["SCHEDULER_INTERVAL_SECONDS"])

    t = threading.Thread(target=loop, name="auto-checkout", daemon=True)
    t.start()
    return t
