"""Background scheduler + Windows toast notifications."""
import atexit
from datetime import datetime, timedelta
from apscheduler.schedulers.background import BackgroundScheduler

import db


# ---- Notification ----
def show_toast(title, body):
    try:
        from windows_toasts import Toast, WindowsToaster, ToastScenario
        toaster = WindowsToaster("Local Assistant")
        toast = Toast()
        toast.text_fields = [title, body]
        toast.scenario = ToastScenario.Reminder   # keeps the toast persistent
        toaster.show_toast(toast)
    except Exception as e:
        print(f"\n\n*** REMINDER: {title} — {body} ***\n", flush=True)
        print(f"(toast failed: {e})", flush=True)

# ---- Scheduler ----
scheduler = BackgroundScheduler(
    job_defaults={
        "misfire_grace_time": 3600,  # fire late if up to 1 hour overdue when system wakes
        "coalesce": True,             # collapse multiple missed runs into one
    }
)


def _fire(reminder_id):
    """Called by APScheduler when a reminder is due."""
    rows = [r for r in db.list_all() if r["id"] == reminder_id]
    if not rows:
        return
    r = rows[0]
    if r["cancelled"]:
        return

    show_toast("Reminder", r["text"])

    if r["kind"] == "once":
        db.mark_fired(reminder_id)
    else:
        # Recurring: reschedule for next occurrence
        next_fire = _next_occurrence(
            datetime.fromisoformat(r["fire_at"]), r["kind"]
        )
        db.reschedule(reminder_id, next_fire)
        _schedule_one(reminder_id, next_fire)


def _next_occurrence(current_fire_at, kind):
    """Compute the next time a recurring reminder should fire."""
    if kind == "yearly":
        return current_fire_at.replace(year=current_fire_at.year + 1)
    if kind == "monthly":
        # naive: add 30 days. Good enough for most cases.
        return current_fire_at + timedelta(days=30)
    if kind == "weekly":
        return current_fire_at + timedelta(weeks=1)
    if kind == "daily":
        return current_fire_at + timedelta(days=1)
    return current_fire_at


def _schedule_one(reminder_id, fire_at):
    """Add or replace a job in the scheduler."""
    job_id = f"reminder_{reminder_id}"
    if scheduler.get_job(job_id):
        scheduler.remove_job(job_id)
    if fire_at <= datetime.now():
        # Past-due — fire immediately
        _fire(reminder_id)
        return
    scheduler.add_job(
        _fire, "date", run_date=fire_at,
        args=[reminder_id], id=job_id, replace_existing=True,
    )


def schedule_all_pending():
    """Called once at startup. Loads all pending reminders into the scheduler."""
    now = datetime.now()
    for r in db.list_pending():
        # Skip already-fired one-offs (they're in the list for display only)
        if r["fired_at"] and r["kind"] == "once":
            continue

        fire_at = datetime.fromisoformat(r["fire_at"])

        if r["fired_at"] and r["kind"] != "once":
            # Recurring that already fired — advance to next occurrence
            while fire_at <= now:
                fire_at = _next_occurrence(fire_at, r["kind"])
            db.reschedule(r["id"], fire_at)
        elif fire_at <= now and r["kind"] == "once":
            # Genuinely missed (never fired) one-off — fire it now
            _fire(r["id"])
            continue

        _schedule_one(r["id"], fire_at)


def start():
    if not scheduler.running:
        scheduler.start()
        atexit.register(lambda: scheduler.shutdown(wait=False))
    schedule_all_pending()


# Convenience for assistant.py
def add_and_schedule(text, fire_at, kind="once"):
    rid = db.add_reminder(text, fire_at, kind)
    _schedule_one(rid, fire_at)
    return rid