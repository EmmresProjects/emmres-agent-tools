#!/usr/bin/env python3
"""tools_schedule.py - scheduling and trigger planning with real time-zone math.

Computes actual next-run timestamps (not just cron strings) for hourly, daily,
weekly, monthly, one-time, cron-expression, and event-driven triggers, including
DST transitions, skipped/overlapping local times, and holiday skips.

Exports ready-to-paste launchers for cron, systemd timers, and Windows Task
Scheduler XML/CLI. It never installs or registers anything on the machine.

Commands
  plan    trigger plan + next N runs for a bundle or explicit schedule options
  next    next N runs for an explicit cadence (no bundle needed)

Requires Python 3.9+ with the standard-library `zoneinfo` database. On hosts
without tzdata, pass an absolute IANA name such as Asia/Manila or America/New_York;
if the database is missing the tool reports `tz_database_missing` instead of
guessing.
"""

from __future__ import annotations

import argparse
import datetime as dt
import os
import re
import sys
import zoneinfo

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import tools_common as C

PROG = "tools_schedule.py"

CADENCES = ("hourly", "daily", "weekly", "monthly", "once", "cron", "event")
MAX_HORIZON_DAYS = 730
MAX_RUNS = 40
WEEKDAYS = {
    "mon": 0, "tue": 1, "wed": 2, "thu": 3, "fri": 4, "sat": 5, "sun": 6,
    "monday": 0, "tuesday": 1, "wednesday": 2, "thursday": 3, "friday": 4,
    "saturday": 5, "sunday": 6,
}


# --------------------------------------------------------------------------
# time helpers
# --------------------------------------------------------------------------

def _tz(name):
    if not name:
        return dt.timezone.utc
    try:
        return zoneinfo.ZoneInfo(name)
    except (zoneinfo.ZoneInfoNotFoundError, ValueError):
        raise C.FoundryError(
            "unknown or unavailable time zone: %r (use an IANA name such as "
            "Asia/Manila or UTC)" % name, code="bad_timezone")


def _parse_hhmm(text):
    match = re.fullmatch(r"(\d{1,2}):(\d{2})", (text or "").strip())
    if not match:
        raise C.FoundryError("time must be HH:MM (24h), got %r" % text,
                            code="bad_time")
    hour, minute = int(match.group(1)), int(match.group(2))
    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        raise C.FoundryError("time out of range: %s" % text, code="bad_time")
    return hour, minute


def _parse_weekdays(text):
    if not text:
        return [0]
    days = []
    for token in re.split(r"[,\s]+", text.strip().lower()):
        if not token:
            continue
        if token.isdigit():
            value = int(token)
            if not 0 <= value <= 6:
                raise C.FoundryError("weekday number must be 0-6 (Mon=0), got %s" % token,
                                    code="bad_weekday")
            days.append(value)
        elif token in WEEKDAYS:
            days.append(WEEKDAYS[token])
        else:
            raise C.FoundryError("unknown weekday %r (use mon..sun or 0..6)" % token,
                                code="bad_weekday")
    if not days:
        raise C.FoundryError("no valid weekday given", code="bad_weekday")
    return sorted(set(days))


def _parse_cron(text):
    """Minimal cron parser: 'm h dom mon dow' with * , - / support."""
    fields = (text or "").split()
    if len(fields) != 5:
        raise C.FoundryError("cron expression needs 5 fields: 'min hour dom mon dow'",
                            code="bad_cron")

    def expand(field, low, high):
        values = set()
        for part in field.split(","):
            step = 1
            if "/" in part:
                part, step_text = part.split("/", 1)
                if not step_text.isdigit():
                    raise C.FoundryError("bad cron step %r" % field, code="bad_cron")
                step = int(step_text)
            if part in ("*", "?"):
                start, end = low, high
            elif "-" in part:
                a, b = part.split("-", 1)
                if not (a.isdigit() and b.isdigit()):
                    raise C.FoundryError("bad cron range %r" % field, code="bad_cron")
                start, end = int(a), int(b)
            else:
                if not part.isdigit():
                    raise C.FoundryError("bad cron field %r" % field, code="bad_cron")
                start = end = int(part)
            if not (low <= start <= high and low <= end <= high) or start > end:
                raise C.FoundryError("cron value out of range in %r" % field,
                                    code="bad_cron")
            values.update(range(start, end + 1, step))
        return sorted(values)

    return {
        "minute": expand(fields[0], 0, 59),
        "hour": expand(fields[1], 0, 23),
        "dom": expand(fields[2], 1, 31),
        "month": expand(fields[3], 1, 12),
        "dow": expand(fields[4], 0, 6),
        "expr": text.strip(),
    }


def _resolve_local(naive, tz):
    """Map a wall-clock time to an aware time, reporting DST edge cases.

    - spring-forward gap (time does not exist): shift forward to the first
      valid instant and record a dst_gap note
    - fall-back overlap (time happens twice): keep the first occurrence and
      record a dst_overlap note
    """
    aware = naive.replace(tzinfo=tz)
    round_trip = aware.astimezone(dt.timezone.utc).astimezone(tz).replace(tzinfo=None)
    if round_trip != naive:
        return round_trip.replace(tzinfo=tz), [{
            "kind": "dst_gap",
            "requested": naive.isoformat(timespec="minutes"),
            "resolved": round_trip.isoformat(timespec="minutes"),
            "action": "shift_forward",
        }]
    notes = []
    later = (aware.astimezone(dt.timezone.utc) + dt.timedelta(hours=1)).astimezone(tz)
    if (later.replace(tzinfo=None) == naive
            and later.utcoffset() != aware.utcoffset()):
        notes.append({
            "kind": "dst_overlap",
            "requested": naive.isoformat(timespec="minutes"),
            "action": "run_once_first_occurrence",
            "first_offset": aware.isoformat(timespec="minutes"),
            "second_offset": later.isoformat(timespec="minutes"),
        })
    return aware, notes


def _runs_for(spec, tz, after, count):
    """Return the next `count` datetimes for the parsed spec."""
    runs = []
    notes = []
    horizon = after + dt.timedelta(days=min(MAX_HORIZON_DAYS, spec.get("horizon_days", 400)))
    cadence = spec["cadence"]
    skipped_holidays = []

    def push(naive):
        nonlocal runs, notes
        aware, dst_notes = _resolve_local(naive, tz)
        notes.extend(dst_notes)
        if aware <= after:
            return False
        runs.append(aware)
        return True

    day = after.astimezone(tz).date()
    guard = 0
    while len(runs) < count and guard < 5000:
        guard += 1
        if day > horizon.date():
            break
        if cadence == "hourly":
            hour, minute = spec["hour"], spec["minute"]
            for h in range(24):
                if (h - hour) % spec["every_hours"] != 0:
                    continue
                naive = dt.datetime.combine(day, dt.time(h, minute))
                push(naive)
                if len(runs) >= count:
                    break
        elif cadence in ("daily", "weekly", "once"):
            applies = True
            if cadence == "weekly":
                applies = day.weekday() in spec["weekdays"]
            if applies and cadence != "once" or (cadence == "once"
                                                 and day == spec["date"]):
                for hour, minute in spec["times"]:
                    if _is_skip_day(day, spec):
                        if len(skipped_holidays) < 10:
                            skipped_holidays.append(day.isoformat())
                        break
                    push(dt.datetime.combine(day, dt.time(hour, minute)))
                    if len(runs) >= count:
                        break
        elif cadence == "monthly":
            target = min(spec["dom"], _last_day(day.year, day.month))
            if day.day == target:
                for hour, minute in spec["times"]:
                    push(dt.datetime.combine(day, dt.time(hour, minute)))
                    if len(runs) >= count:
                        break
        elif cadence == "cron":
            cron = spec["cron"]
            for hour in cron["hour"]:
                for minute in cron["minute"]:
                    if day.day not in cron["dom"] and cron["dom"] != list(range(1, 32)):
                        continue
                    if day.month not in cron["month"]:
                        continue
                    cron_days = {_python_dow(d) for d in cron["dow"]}
                    if day.weekday() not in cron_days and len(cron["dow"]) < 7:
                        continue
                    push(dt.datetime.combine(day, dt.time(hour, minute)))
                    if len(runs) >= count:
                        break
                if len(runs) >= count:
                    break
        day = day + dt.timedelta(days=1)
    return runs[:count], notes, skipped_holidays


def _last_day(year, month):
    nxt = dt.date(year + (month // 12), (month % 12) + 1, 1)
    return (nxt - dt.timedelta(days=1)).day


def _is_skip_day(day, spec):
    holidays = set(spec.get("skip_dates") or [])
    if day.isoformat() in holidays:
        return True
    if spec.get("skip_weekends") and day.weekday() >= 5:
        return True
    return False


# --------------------------------------------------------------------------
# exports
# --------------------------------------------------------------------------

def _cron_dow(python_weekday):
    """Convert Python weekday (0=Mon) to cron day-of-week (0=Sun)."""
    return (int(python_weekday) + 1) % 7


def _python_dow(cron_dow):
    """Convert cron day-of-week (0=Sun) to Python weekday (0=Mon)."""
    return (int(cron_dow) - 1) % 7


def _cron_expression(spec):
    cadence = spec["cadence"]
    if cadence == "hourly":
        return "0 * * * *" if spec["minute"] == 0 else "%d */%d * * *" % (
            spec["minute"], spec["every_hours"])
    if cadence == "daily":
        hour, minute = spec["times"][0]
        days = ",".join(str(_cron_dow(d)) for d in spec["weekdays"])
        return "%d %d * * %s" % (minute, hour, days if cadence == "weekly" else "*")
    if cadence == "weekly":
        hour, minute = spec["times"][0]
        days = ",".join(str(_cron_dow(d)) for d in spec["weekdays"])
        return "%d %d * * %s" % (minute, hour, days)
    if cadence == "monthly":
        hour, minute = spec["times"][0]
        return "%d %d %d * *" % (minute, hour, spec["dom"])
    if cadence == "cron":
        return spec["cron"]["expr"]
    if cadence == "once":
        hour, minute = spec["times"][0]
        return "%d %d %d %d *" % (minute, hour, spec["date"].day, spec["date"].month)
    return None


def _launchers(spec, cfg, name, command):
    tz = spec["timezone"]
    cd = spec["cadence"]
    cron = _cron_expression(spec)
    lines = []
    lines.append("# --- Linux/macOS crontab (time zone: %s; dow 0=Sun..6=Sat) ---" % tz)
    lines.append("# Verify: date; cron uses the system time zone unless CRON_TZ is honoured.")
    lines.append("CRON_TZ=%s" % tz if tz != "UTC" else "# CRON_TZ not needed for UTC hosts")
    if cd == "event":
        lines.append("# event-driven: no cron entry; wire the watcher to: %s" % command)
    else:
        lines.append("%s cd %s && %s >> %s/%s.log 2>&1"
                     % (cron, "$HOME/%s" % cfg["workspace_dir"], command,
                        "$HOME/%s" % cfg["workspace_dir"], name))
    lines.append("")
    lines.append("# --- systemd timer (UTC-safe, DST-safe) ---")
    if cd == "daily":
        hour, minute = spec["times"][0]
        lines.append("OnCalendar=*-*-* %02d:%02d:00 %s" % (hour, minute, tz))
    elif cd == "weekly":
        hour, minute = spec["times"][0]
        days = ",".join(["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"][d]
                        for d in spec["weekdays"])
        lines.append("OnCalendar=%s *-*-* %02d:%02d:00 %s" % (days, hour, minute, tz))
    elif cd == "hourly":
        lines.append("OnCalendar=*-*-* *:%02d:00 %s" % (spec["minute"], tz))
    elif cd == "monthly":
        hour, minute = spec["times"][0]
        lines.append("OnCalendar=*-*-%02d %02d:%02d:00 %s" % (spec["dom"], hour, minute, tz))
    else:
        lines.append("# no systemd calendar expression for cadence=%s" % cd)
    lines.append("Persistent=true   # run once after a missed window")
    lines.append("")
    lines.append("# --- Windows Task Scheduler (schtasks) ---")
    launch = "%s \"%s\" --config %s" % (
        cfg["python_executable"], command, "asset-default-config.json")
    if cd in ("daily", "weekly", "monthly", "hourly"):
        schedule = {"daily": "DAILY", "weekly": "WEEKLY", "monthly": "MONTHLY",
                    "hourly": "HOURLY"}[cd]
        lines.append("schtasks /Create /TN \"%s\" /SC %s /MO 1 /ST %s /TR \"%s\" /F"
                     % (name, schedule, "%02d:%02d" % spec["times"][0], launch))
    else:
        lines.append("# use /SC ONCE with /SD /ST for cadence=%s" % cd)
    return "\n".join(lines)


# --------------------------------------------------------------------------
# spec assembly
# --------------------------------------------------------------------------

def _spec_from_args(args, rules=None):
    limits = (rules or {}).get("limits") or {}
    cadence = args.cadence
    if cadence not in CADENCES:
        raise C.FoundryError("cadence must be one of: %s" % ", ".join(CADENCES),
                            code="bad_cadence")
    timezone = _tz(args.timezone).key if args.timezone else "UTC"
    spec = {
        "cadence": cadence,
        "timezone": timezone,
        "minute": 0,
        "hour": 0,
        "every_hours": 1,
        "times": [],
        "weekdays": [],
        "dom": None,
        "date": None,
        "cron": None,
        "skip_weekends": args.skip_weekends,
        "skip_dates": list(args.skip_date or []),
        "horizon_days": int(limits.get("max_schedule_horizon_days", MAX_HORIZON_DAYS)),
    }
    if args.every_hours and not 1 <= args.every_hours <= 24:
        raise C.FoundryError("--every-hours must be 1-24", code="bad_input")
    spec["every_hours"] = args.every_hours or 1
    if args.at:
        spec["times"] = [_parse_hhmm(t) for t in args.at]
    if cadence != "once" and not spec["times"]:
        spec["times"] = [(2, 0)] if cadence in ("daily", "weekly", "monthly") else []
    if spec["times"]:
        spec["hour"], spec["minute"] = spec["times"][0]
    if cadence == "hourly" and args.at:
        spec["minute"] = _parse_hhmm(args.at[0])[1]
    if cadence in ("weekly",):
        spec["weekdays"] = _parse_weekdays(args.weekdays)
    if cadence == "monthly":
        if not args.day_of_month:
            raise C.FoundryError("monthly cadence needs --day-of-month 1-31",
                                code="bad_input")
        if not 1 <= args.day_of_month <= 31:
            raise C.FoundryError("--day-of-month must be 1-31", code="bad_input")
        spec["dom"] = args.day_of_month
    if cadence == "once":
        try:
            spec["date"] = dt.date.fromisoformat(args.on)
        except (TypeError, ValueError):
            raise C.FoundryError("--on must be an ISO date (YYYY-MM-DD)",
                                code="bad_date")
        if not spec["times"]:
            raise C.FoundryError("one-time cadence needs --at HH:MM", code="bad_input")
    if cadence == "cron":
        if not args.cron:
            raise C.FoundryError("cron cadence needs --cron \"m h dom mon dow\"",
                                code="bad_input")
        spec["cron"] = _parse_cron(args.cron)
    if cadence == "event":
        if not args.event:
            raise C.FoundryError(
                "event cadence needs --event (file_change, webhook, email, form, "
                "database, device)", code="bad_input")
        spec["event"] = args.event
    return spec


def _plan_payload(spec, cfg, name, command, bundle_goal=None):
    tz = _tz(spec["timezone"])
    after = dt.datetime.now(dt.timezone.utc)
    count = min(max(spec.get("count", 8), 1), MAX_RUNS)
    if spec["cadence"] == "event":
        runs, notes, skipped = [], [], []
    else:
        runs, notes, skipped = _runs_for(spec, tz, after, count)
    overlap = "skip" if spec.get("overlap") != "queue" else "queue"
    payload = {
        "ok": True,
        "tool": PROG,
        "generated_at": C.now_iso(),
        "job": {"name": name, "goal": bundle_goal, "command": command},
        "trigger": {k: v for k, v in spec.items() if k not in ("cron", "date")},
        "cron_expression": _cron_expression(spec),
        "next_runs": [r.isoformat() for r in runs],
        "next_runs_local": [r.strftime("%Y-%m-%d %H:%M:%S %Z") for r in runs],
        "dst_notes": notes,
        "skipped_days": skipped,
        "policy": {
            "timezone": spec["timezone"],
            "overlap": overlap,
            "missed_run": "run_once_on_recovery" if spec.get("catch_up") else "skip",
            "max_retries": (spec.get("limits") or {}).get("max_retries", 3),
            "timeout_seconds": (spec.get("limits") or {}).get(
                "default_job_timeout_seconds", 900),
            "lock_file": "%s.lock" % name,
            "max_concurrency": 1,
        },
        "profile": spec.get("profile"),
        "profile_notes": spec.get("profile_notes"),
        "launchers": _launchers(spec, cfg, name, command).splitlines(),
        "notes": [
            "Times are computed with the standard-library zoneinfo database.",
            "This tool never registers, enables, or triggers a job on the host.",
            "DST gaps are shifted forward; overlapping hours run once.",
        ],
    }
    if not runs and spec["cadence"] != "event":
        payload["warning"] = ("no runs computed within the horizon; check the "
                              "cadence, date, or holiday skips")
    return C.guard_output_size(payload, cfg)


# --------------------------------------------------------------------------
# commands
# --------------------------------------------------------------------------

def _schedule_arguments(p):
    p.add_argument("--cadence", default="daily", choices=CADENCES,
                   help="trigger type (default daily)")
    p.add_argument("--timezone", default="UTC", metavar="IANA",
                   help="IANA time zone, e.g. Asia/Manila (default UTC)")
    p.add_argument("--at", action="append", metavar="HH:MM",
                   help="run time; repeatable (daily/weekly/monthly)")
    p.add_argument("--every-hours", type=int, metavar="N", help="hourly step (1-24)")
    p.add_argument("--weekdays", metavar="LIST", help="mon,tue,... or 0-6 (weekly)")
    p.add_argument("--day-of-month", type=int, metavar="D", help="monthly day (1-31)")
    p.add_argument("--on", metavar="YYYY-MM-DD", help="one-time date")
    p.add_argument("--cron", metavar="EXPR", help="cron expression 'm h dom mon dow'")
    p.add_argument("--event", metavar="KIND", help="event trigger kind (file_change, webhook, ...)")
    p.add_argument("--skip-weekends", action="store_true", help="skip Sat/Sun for daily jobs")
    p.add_argument("--skip-date", action="append", metavar="YYYY-MM-DD",
                   help="holiday to skip; repeatable")
    p.add_argument("--count", type=int, default=8, metavar="N", help="next runs to compute (1-40)")
    p.add_argument("--overlap", choices=("skip", "queue"), default="skip",
                   help="policy when the previous run is still active")
    p.add_argument("--catch-up", action="store_true", help="run once after a missed window")
    p.add_argument("--profile", metavar="ID",
                   help="apply a preset from asset-scheduler-profiles.json "
                        "(e.g. nightly-batch, weekday-business-hours)")
    p.add_argument("--name", metavar="NAME", help="job name (default from config)")
    p.add_argument("--command", metavar="CMD", help="command the schedule should launch")


def _apply_profile(args):
    """Fill unset schedule options from asset-scheduler-profiles.json."""
    if not getattr(args, "profile", None):
        return args
    data = C.load_json(C.asset(C.SCHEDULE_FILE), "scheduler profiles")
    profiles = {p["id"]: p for p in data.get("profiles", [])}
    if args.profile not in profiles:
        raise C.FoundryError(
            "unknown profile %r; available: %s"
            % (args.profile, ", ".join(sorted(profiles))),
            code="unknown_profile")
    profile = profiles[args.profile]
    args.cadence = profile.get("cadence", args.cadence)
    if not args.at and profile.get("at"):
        args.at = [profile["at"]]
    args.timezone = args.timezone if args.timezone_explicit else profile.get(
        "timezone", args.timezone)
    if not args.weekdays and profile.get("weekdays"):
        args.weekdays = profile["weekdays"]
    if not args.day_of_month and profile.get("day_of_month"):
        args.day_of_month = profile["day_of_month"]
    if not args.every_hours and profile.get("every_hours"):
        args.every_hours = profile["every_hours"]
    if profile.get("event") and not args.event:
        args.event = profile["event"]
    args.overlap = profile.get("overlap", args.overlap)
    args.catch_up = args.catch_up or bool(profile.get("catch_up"))
    args.profile_notes = profile.get("notes")
    return args


def cmd_plan(args, cfg):
    args = _apply_profile(args)
    rules = C.load_rules(root=C.tool_root())
    if not args.input and not args.at and args.cadence != "cron":
        raise C.FoundryError(
            "provide --input BUNDLE (for goal/context) or explicit --at/--cron options",
            code="missing_input")
    spec = _spec_from_args(args, rules)
    bundle = C.load_bundle(args.input) if args.input else None
    spec["limits"] = rules.get("limits") or {}
    spec["count"] = args.count
    spec["overlap"] = args.overlap
    spec["catch_up"] = args.catch_up
    spec["profile"] = getattr(args, "profile", None)
    spec["profile_notes"] = getattr(args, "profile_notes", None)
    name = args.name or cfg["scheduler_name"]
    command = args.command or ("%s %s" % (cfg["python_executable"],
                                          "automation_entry.py"))
    return _plan_payload(spec, cfg, name, command,
                         bundle_goal=bundle["goal"] if bundle else None)


def cmd_next(args, cfg):
    args = _apply_profile(args)
    spec = _spec_from_args(args, C.load_rules(root=C.tool_root()))
    spec["count"] = args.count
    spec["overlap"] = args.overlap
    tz = _tz(spec["timezone"])
    runs, notes, skipped = _runs_for(spec, tz, dt.datetime.now(dt.timezone.utc),
                                     min(max(args.count, 1), MAX_RUNS))
    return {
        "ok": True,
        "tool": PROG,
        "timezone": spec["timezone"],
        "cron_expression": _cron_expression(spec),
        "count": len(runs),
        "next_runs": [r.isoformat() for r in runs],
        "next_runs_local": [r.strftime("%Y-%m-%d %H:%M:%S %Z") for r in runs],
        "dst_notes": notes,
        "skipped_days": skipped,
    }


# --------------------------------------------------------------------------
# parser
# --------------------------------------------------------------------------

def build_parser():
    parser = argparse.ArgumentParser(
        prog=PROG,
        description="Compute real next-run times and launchers for automation triggers.",
        epilog="Examples:\n"
               "  python3 tools_schedule.py plan --input bundle.json --cadence daily "
               "--at 02:30 --timezone Asia/Manila\n"
               "  python3 tools_schedule.py next --cadence weekly --weekdays mon,thu "
               "--at 09:00 --timezone America/New_York --count 6\n"
               "  python3 tools_schedule.py next --cadence cron --cron \"*/15 9-17 * * mon-fri\"",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--version", action="version",
                        version="%s %s" % (C.TOOL_NAME, C.TOOL_VERSION))
    C.add_common_arguments(parser)
    common = argparse.ArgumentParser(add_help=False)
    C.add_common_arguments(common, suppress=True)
    sub = parser.add_subparsers(dest="command", metavar="COMMAND")

    p = sub.add_parser("plan", parents=[common],
                       help="trigger plan + next runs for a bundle or explicit options")
    p.add_argument("--input", metavar="BUNDLE", help="bundle JSON file (optional context)")
    _schedule_arguments(p)
    p.set_defaults(func=cmd_plan)

    p = sub.add_parser("next", parents=[common],
                       help="next N runs for an explicit cadence")
    _schedule_arguments(p)
    p.set_defaults(func=cmd_next)
    return parser


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    explicit_tz = any(a == "--timezone" or a.startswith("--timezone=") for a in argv)
    def _handler(args, cfg):
        args.timezone_explicit = explicit_tz or getattr(args, "timezone_explicit", False)
        return args.func(args, cfg)
    return C.run_cli(PROG, build_parser, _handler, argv)


if __name__ == "__main__":
    sys.exit(main())
