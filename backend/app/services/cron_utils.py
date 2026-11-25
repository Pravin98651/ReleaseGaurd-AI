"""Cron expression parser, IANA timezone validator, next-run computation, and NL plan validation."""

from __future__ import annotations

import json
import logging
import re
from datetime import UTC, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from app.agents.gemini_client import GeminiClient
from app.core.security import wrap_untrusted

_CRON_MACROS: dict[str, str] = {
    "@hourly": "0 * * * *",
    "@daily": "0 0 * * *",
    "@midnight": "0 0 * * *",
    "@weekly": "0 0 * * 0",
    "@monthly": "0 0 1 * *",
}

_DOW_NAMES: dict[str, int] = {
    "sun": 0,
    "mon": 1,
    "tue": 2,
    "wed": 3,
    "thu": 4,
    "fri": 5,
    "sat": 6,
}

_MONTH_NAMES: dict[str, int] = {
    "jan": 1,
    "feb": 2,
    "mar": 3,
    "apr": 4,
    "may": 5,
    "jun": 6,
    "jul": 7,
    "aug": 8,
    "sep": 9,
    "oct": 10,
    "nov": 11,
    "dec": 12,
}


def validate_iana_timezone(tz_name: str) -> ZoneInfo:
    """Validate and return ZoneInfo for an IANA timezone string (e.g., 'Asia/Kolkata', 'UTC')."""
    cleaned = (tz_name or "").strip()
    if not cleaned:
        raise ValueError("Timezone is required (e.g., 'UTC' or 'Asia/Kolkata').")
    try:
        return ZoneInfo(cleaned)
    except ZoneInfoNotFoundError as exc:
        raise ValueError(f"Unknown IANA timezone '{cleaned}'.") from exc


def _parse_cron_field(
    token: str,
    *,
    min_val: int,
    max_val: int,
    names: dict[str, int] | None = None,
    allow_seven_sunday: bool = False,
) -> set[int]:
    out: set[int] = set()
    for raw_part in token.split(","):
        part = raw_part.strip().lower()
        if not part:
            raise ValueError(f"Empty cron segment in '{token}'.")
        if names:
            for k, v in names.items():
                part = re.sub(rf"\b{k}\b", str(v), part)

        step = 1
        if "/" in part:
            base, step_str = part.split("/", 1)
            step = int(step_str)
            if step <= 0:
                raise ValueError(f"Cron step must be > 0 in '{raw_part}'.")
        else:
            base = part

        if base == "*":
            lo, hi = min_val, max_val
        elif "-" in base:
            lo_s, hi_s = base.split("-", 1)
            lo, hi = int(lo_s), int(hi_s)
        else:
            val = int(base)
            if allow_seven_sunday and val == 7:
                val = 0
            if val < min_val or val > max_val:
                raise ValueError(f"Cron value {val} out of bounds [{min_val}..{max_val}].")
            out.add(val)
            continue

        if allow_seven_sunday and hi == 7:
            hi = 7
        if lo < min_val or hi > (7 if allow_seven_sunday else max_val) or lo > hi:
            raise ValueError(f"Cron range {lo}-{hi} out of bounds [{min_val}..{max_val}].")
        for n in range(lo, hi + 1, step):
            out.add(0 if (allow_seven_sunday and n == 7) else n)
    if not out:
        raise ValueError(f"Invalid cron token '{token}'.")
    return out


class ParsedCron:
    """Validated 5-field standard cron expression."""

    def __init__(self, expression: str) -> None:
        raw = (expression or "").strip()
        expanded = _CRON_MACROS.get(raw.lower(), raw)
        parts = expanded.split()
        if len(parts) != 5:
            raise ValueError(
                f"Cron expression must have 5 fields (minute hour day month weekday), got '{raw}'."
            )
        self.raw = expanded
        self.minutes = _parse_cron_field(parts[0], min_val=0, max_val=59)
        self.hours = _parse_cron_field(parts[1], min_val=0, max_val=23)
        self.days = _parse_cron_field(parts[2], min_val=1, max_val=31)
        self.months = _parse_cron_field(parts[3], min_val=1, max_val=12, names=_MONTH_NAMES)
        self.dows = _parse_cron_field(
            parts[4], min_val=0, max_val=6, names=_DOW_NAMES, allow_seven_sunday=True
        )
        self.dom_wildcard = parts[2].strip() == "*"
        self.dow_wildcard = parts[4].strip() == "*"

    def matches_local(self, dt_local: datetime) -> bool:
        py_dow = (dt_local.weekday() + 1) % 7  # Python Mon=0 -> Sun=0
        if dt_local.minute not in self.minutes:
            return False
        if dt_local.hour not in self.hours:
            return False
        if dt_local.month not in self.months:
            return False
        dom_match = dt_local.day in self.days
        dow_match = py_dow in self.dows
        if not self.dom_wildcard and not self.dow_wildcard:
            return dom_match or dow_match
        return dom_match and dow_match

    def next_run_after(self, after_utc: datetime, tz: ZoneInfo) -> datetime:
        """Compute next strictly future trigger in UTC respecting local IANA DST boundaries."""
        base = after_utc.astimezone(UTC) if after_utc.tzinfo else after_utc.replace(tzinfo=UTC)
        cur_local = base.astimezone(tz).replace(second=0, microsecond=0) + timedelta(minutes=1)
        # Iterate at most 366 days (~527,040 minutes; jump by day/hour when mismatches allow)
        max_steps = 400_000
        steps = 0
        while steps < max_steps:
            if cur_local.month not in self.months:
                next_month = cur_local.replace(day=1, hour=0, minute=0) + timedelta(days=32)
                cur_local = next_month.replace(day=1, hour=0, minute=0)
                steps += 1440
                continue
            py_dow = (cur_local.weekday() + 1) % 7
            dom_ok = cur_local.day in self.days
            dow_ok = py_dow in self.dows
            day_ok = (dom_ok or dow_ok) if (not self.dom_wildcard and not self.dow_wildcard) else (dom_ok and dow_ok)
            if not day_ok:
                cur_local = (cur_local + timedelta(days=1)).replace(hour=0, minute=0)
                steps += 1440
                continue
            if cur_local.hour not in self.hours:
                cur_local = (cur_local + timedelta(hours=1)).replace(minute=0)
                steps += 60
                continue
            if cur_local.minute not in self.minutes:
                cur_local += timedelta(minutes=1)
                steps += 1
                continue
            return cur_local.astimezone(UTC)
        raise ValueError(f"Could not find next cron occurrence within 1 year for '{self.raw}'.")


def build_cron_from_frequency(
    *,
    frequency: str,
    cron_expression: str | None = None,
    interval_minutes: int | None = None,
    start_at: datetime | None = None,
    timezone: str = "UTC",
) -> str | None:
    """Resolve canonical cron expression for cron/hourly/daily/weekly/monthly schedules."""
    tz = validate_iana_timezone(timezone)
    ref_local = (start_at.astimezone(tz) if start_at and start_at.tzinfo else (start_at or datetime.now(UTC)).replace(tzinfo=UTC).astimezone(tz))
    freq = frequency.strip().lower()
    if freq == "cron":
        if not cron_expression or not cron_expression.strip():
            raise ValueError("cron_expression is required when frequency='cron'.")
        return ParsedCron(cron_expression).raw
    if freq == "hourly":
        return f"{ref_local.minute} * * * *"
    if freq == "daily":
        return f"{ref_local.minute} {ref_local.hour} * * *"
    if freq == "weekly":
        py_dow = (ref_local.weekday() + 1) % 7
        return f"{ref_local.minute} {ref_local.hour} * * {py_dow}"
    if freq == "monthly":
        return f"{ref_local.minute} {ref_local.hour} {ref_local.day} * *"
    if freq == "interval_minutes":
        if interval_minutes is None or interval_minutes < 1 or interval_minutes > 1440:
            raise ValueError("interval_minutes must be between 1 and 1440.")
        return None
    if freq == "once":
        return None
    raise ValueError(f"Unsupported frequency '{frequency}'.")


def compute_next_run_at(
    *,
    frequency: str,
    cron_expression: str | None = None,
    interval_minutes: int | None = None,
    start_at: datetime | None = None,
    end_at: datetime | None = None,
    timezone: str = "UTC",
    reference_utc: datetime | None = None,
    initial: bool = False,
) -> datetime | None:
    """Compute next trigger timestamp strictly in UTC, respecting start_at, end_at & timezone."""
    tz = validate_iana_timezone(timezone)
    now = reference_utc or datetime.now(UTC)
    if now.tzinfo is None:
        now = now.replace(tzinfo=UTC)
    start_utc = (
        start_at.astimezone(UTC) if start_at and start_at.tzinfo else (start_at.replace(tzinfo=UTC) if start_at else None)
    )
    end_utc = (
        end_at.astimezone(UTC) if end_at and end_at.tzinfo else (end_at.replace(tzinfo=UTC) if end_at else None)
    )

    freq = frequency.strip().lower()
    if freq == "once":
        if not initial:
            return None
        candidate = start_utc if (start_utc and start_utc > now) else now + timedelta(seconds=5)
        if end_utc and candidate > end_utc:
            return None
        return candidate

    anchor = start_utc if (initial and start_utc and start_utc > now) else now
    if freq == "interval_minutes":
        mins = max(1, int(interval_minutes or 15))
        nxt = anchor if (initial and start_utc and start_utc > now) else (anchor + timedelta(minutes=mins))
    else:
        resolved_cron = build_cron_from_frequency(
            frequency=freq,
            cron_expression=cron_expression,
            interval_minutes=interval_minutes,
            start_at=start_utc,
            timezone=timezone,
        )
        assert resolved_cron is not None
        parsed = ParsedCron(resolved_cron)
        # If initial start_at > now and matches cron, honor start_at exact minute
        if initial and start_utc and start_utc > now:
            nxt = parsed.next_run_after(start_utc - timedelta(minutes=1), tz)
        else:
            nxt = parsed.next_run_after(now, tz)

    if end_utc and nxt > end_utc:
        return None
    return nxt


def preview_next_runs(
    *,
    frequency: str,
    cron_expression: str | None = None,
    interval_minutes: int | None = None,
    start_at: datetime | None = None,
    end_at: datetime | None = None,
    timezone: str = "UTC",
    count: int = 5,
) -> list[dict[str, str]]:
    """Return next `count` execution timestamps in both UTC ISO and localized string."""
    tz = validate_iana_timezone(timezone)
    runs: list[dict[str, str]] = []
    cursor: datetime | None = datetime.now(UTC)
    for idx in range(count):
        nxt = compute_next_run_at(
            frequency=frequency,
            cron_expression=cron_expression,
            interval_minutes=interval_minutes,
            start_at=start_at,
            end_at=end_at,
            timezone=timezone,
            reference_utc=cursor,
            initial=(idx == 0),
        )
        if nxt is None:
            break
        local_dt = nxt.astimezone(tz)
        runs.append(
            {
                "utc": nxt.isoformat(),
                "local": local_dt.strftime("%Y-%m-%d %H:%M %Z"),
            }
        )
        cursor = nxt
    return runs


def describe_schedule(
    *,
    frequency: str,
    cron_expression: str | None = None,
    interval_minutes: int | None = None,
    start_at: datetime | None = None,
    timezone: str = "UTC",
) -> str:
    """Render concise human-readable description including IANA timezone."""
    tz = validate_iana_timezone(timezone)
    freq = frequency.strip().lower()
    if freq == "once":
        if start_at:
            loc = (start_at.replace(tzinfo=UTC) if start_at.tzinfo is None else start_at).astimezone(tz)
            return f"Once on {loc.strftime('%Y-%m-%d at %H:%M')} {timezone}"
        return f"One-time execution ({timezone})"
    if freq == "interval_minutes":
        return f"Every {int(interval_minutes or 15)} minutes ({timezone})"

    resolved = build_cron_from_frequency(
        frequency=freq,
        cron_expression=cron_expression,
        interval_minutes=interval_minutes,
        start_at=start_at,
        timezone=timezone,
    )
    if resolved:
        parts = resolved.split()
        minute, hour, dom, month, dow = parts
        if dom == "*" and month == "*" and dow.lower() in ("1-5", "mon-fri"):
            return f"Every weekday at {hour.zfill(2)}:{minute.zfill(2)} {timezone}"
        if freq == "daily" or (dom == "*" and month == "*" and dow == "*" and hour.isdigit() and minute.isdigit()):
            return f"Every day at {hour.zfill(2)}:{minute.zfill(2)} {timezone}"
        if freq == "hourly" or (hour == "*" and dom == "*" and minute.isdigit()):
            return f"Hourly at :{minute.zfill(2)} past the hour ({timezone})"
        return f"Cron '{resolved}' ({timezone})"
    return f"{freq} ({timezone})"


_FORBIDDEN_SCHEDULE_PATTERNS: tuple[tuple[str, str], ...] = (
    ("rm -rf", "Destructive shell command"),
    ("curl ", "Arbitrary network shell execution"),
    ("wget ", "Arbitrary network shell execution"),
    ("bash ", "Arbitrary shell execution"),
    ("powershell", "Arbitrary shell execution"),
    ("subprocess", "Subprocess execution"),
    ("os.system", "OS command execution"),
    ("eval(", "Dynamic code evaluation"),
    ("exec(", "Dynamic code execution"),
    ("auto-merge", "Automated PR merge is forbidden by policy"),
    ("auto merge", "Automated PR merge is forbidden by policy"),
    ("merge pull request", "PR merging is forbidden by policy"),
    ("auto-approve", "Scheduled jobs cannot self-approve policy gates"),
    ("auto approve", "Scheduled jobs cannot self-approve policy gates"),
    ("self-approve", "Scheduled jobs cannot self-approve policy gates"),
    ("bypass approval", "Approval bypass is forbidden by policy"),
    ("delete production", "Unapproved production flag deletion is forbidden"),
    ("delete prod", "Unapproved production flag deletion is forbidden"),
)


def validate_custom_schedule_prompt(nl_prompt: str) -> tuple[bool, str | None]:
    """Validate custom NL schedule prompts against ReleaseGuard policy guardrails."""
    cleaned = (nl_prompt or "").strip()
    if len(cleaned) < 6:
        return False, "Custom schedule prompt must describe what safe ReleaseGuard task to run."
    lower = cleaned.lower()
    for token, reason in _FORBIDDEN_SCHEDULE_PATTERNS:
        if token in lower:
            return False, f"Unsafe or forbidden schedule instruction rejected ({reason})."
    return True, None


async def parse_nl_schedule_plan(
    nl_prompt: str,
    *,
    timezone: str = "UTC",
    gemini_client: GeminiClient | None = None,
) -> dict[str, Any]:
    """Translate NL automation request into a validated structured execution plan (never shell code)."""
    cleaned = nl_prompt.strip()
    ok, err_msg = validate_custom_schedule_prompt(cleaned)
    if not ok:
        raise ValueError(err_msg or "Unsafe or forbidden instruction rejected by scheduler policy validator.")
    lower = cleaned.lower()

    tz = validate_iana_timezone(timezone)
    del tz

    # Deterministic NL parsing baseline
    frequency = "daily"
    cron_expr = "0 9 * * *"
    interval_mins: int | None = None
    m_min = re.search(r"every\s+(\d{1,3})\s+min", lower)
    m_time = re.search(r"\b(\d{1,2})(?::(\d{2}))?\s*(am|pm)?\b", lower)
    hour = 9
    minute = 0
    if m_time:
        hour = int(m_time.group(1))
        minute = int(m_time.group(2) or 0)
        ampm = m_time.group(3)
        if ampm == "pm" and hour < 12:
            hour += 12
        elif ampm == "am" and hour == 12:
            hour = 0
        hour = max(0, min(23, hour))
        minute = max(0, min(59, minute))

    if m_min:
        frequency = "interval_minutes"
        interval_mins = max(1, min(1440, int(m_min.group(1))))
        cron_expr = None
    elif "weekday" in lower or "mon-fri" in lower or "monday to friday" in lower:
        frequency = "cron"
        cron_expr = f"{minute} {hour} * * 1-5"
    elif "weekly" in lower or "every monday" in lower:
        frequency = "cron"
        cron_expr = f"{minute} {hour} * * 1"
    elif "hourly" in lower:
        frequency = "hourly"
        cron_expr = f"{minute} * * * *"
    elif "daily" in lower or "every day" in lower or "at " in lower:
        frequency = "daily"
        cron_expr = f"{minute} {hour} * * *"

    allowed_steps: list[str] = []
    if "repo" in lower or "sync" in lower:
        allowed_steps.append("sync_repositories")
    if "pull request" in lower or "pr" in lower:
        allowed_steps.append("analyze_new_pull_requests")
    if "flag" in lower or "health" in lower:
        allowed_steps.append("scan_flag_health")
    if "experiment" in lower or "bayesian" in lower:
        allowed_steps.append("monitor_experiments")
    if "summary" in lower or "report" in lower or not allowed_steps:
        allowed_steps.append("generate_release_health_report")

    plan: dict[str, Any] = {
        "frequency": frequency,
        "cron_expression": cron_expr,
        "interval_minutes": interval_mins,
        "timezone": timezone,
        "allowed_operations": allowed_steps,
        "sanitized_prompt": cleaned[:1500],
        "requires_human_approval_for_prod": True,
    }

    if gemini_client is not None and gemini_client.is_configured:
        prompt = (
            "Extract a safe ReleaseGuard scheduled workflow plan as STRICT JSON with keys: "
            "frequency ('interval_minutes'|'hourly'|'daily'|'weekly'|'cron'), "
            "cron_expression (5-field string or null), interval_minutes (int or null), "
            "allowed_operations (subset of ['sync_repositories','analyze_new_pull_requests','scan_flag_health','monitor_experiments','generate_release_health_report']), "
            "summary (str).\n"
            f"Requested timezone: {timezone}\n"
            f"{wrap_untrusted('operator_schedule_prompt', {'timezone': timezone}, cleaned, max_chars=1500)}"
        )
        try:
            resp = await gemini_client.generate_content(
                contents=[{"role": "user", "parts": [{"text": prompt}]}],
                system_instruction=(
                    "You convert release engineering schedule requests into safe JSON plans. "
                    "Never output shell commands or approve production mutations."
                ),
                temperature=0.1,
                response_mime_type="application/json",
            )
            raw = getattr(resp, "text", None) or ""
            if raw:
                parsed = json.loads(raw)
                if isinstance(parsed, dict):
                    valid_ops = {
                        "sync_repositories",
                        "analyze_new_pull_requests",
                        "scan_flag_health",
                        "monitor_experiments",
                        "generate_release_health_report",
                    }
                    if isinstance(parsed.get("allowed_operations"), list):
                        ops = [str(o) for o in parsed["allowed_operations"] if str(o) in valid_ops]
                        if ops:
                            plan["allowed_operations"] = ops
                    if parsed.get("cron_expression"):
                        candidate = ParsedCron(str(parsed["cron_expression"])).raw
                        plan["frequency"] = "cron"
                        plan["cron_expression"] = candidate
                        plan["interval_minutes"] = None
                    if parsed.get("summary"):
                        plan["summary"] = str(parsed["summary"])[:400]
        except Exception as exc:  # noqa: BLE001
            logging.getLogger(__name__).debug("Gemini NL schedule assist skipped: %s", exc)

    plan["human_description"] = describe_schedule(
        frequency=plan["frequency"],
        cron_expression=plan.get("cron_expression"),
        interval_minutes=plan.get("interval_minutes"),
        timezone=timezone,
    )
    return plan
