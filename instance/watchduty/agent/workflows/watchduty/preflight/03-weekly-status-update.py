#!/usr/bin/env python3
"""Pre-flight: watch for Advisor and MPEX weekly status reports.

The HP weekly status repo uses Friday-dated report folders, e.g.:
  reports/2026/2026-10-30/mpex/lightspeed-advisor.md
  reports/2026/2026-10-30/mpex.md

This preflight polls exact expected paths during Friday working hours and posts
new reports to Slack. On Monday it performs one AI-assisted fallback check for
any still-missing report, then marks that weekly watcher closed so the bot does
not keep searching every hour.

Exit conditions:
  - Outside Friday/Monday watch windows -> skip
  - Expected report present and new -> send Slack message, persist marker, skip
  - Expected report already posted -> skip
  - Expected report missing on Friday -> skip and keep polling hourly
  - Expected report still missing during Monday fallback -> start one AI session
    to search for moved/renamed/misfiled reports, mark that report check closed,
    and skip the rest of the week. Missing reports are OK; silent format drift is
    what we want to catch.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import urllib.request
from datetime import date, datetime, time, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

TIMEZONE = os.environ.get("WEEKLY_STATUS_TIMEZONE", "Europe/Prague")
REPORT_REPO = os.environ.get("WEEKLY_STATUS_REPO", "openshift-eng/hp-weekly-status")
REPORT_REF = os.environ.get("WEEKLY_STATUS_REF", "main")

# Friday exact-path polling, Monday one-shot AI fallback. Weekday uses
# datetime.weekday(): Monday=0, Friday=4.
POLL_WEEKDAY = int(os.environ.get("WEEKLY_STATUS_POLL_WEEKDAY", "4"))
POLL_START_HOUR = int(os.environ.get("WEEKLY_STATUS_POLL_START_HOUR", "8"))
POLL_END_HOUR = int(os.environ.get("WEEKLY_STATUS_POLL_END_HOUR", "16"))
AI_CHECK_WEEKDAY = int(os.environ.get("WEEKLY_STATUS_AI_CHECK_WEEKDAY", "0"))
AI_CHECK_START_HOUR = int(os.environ.get("WEEKLY_STATUS_AI_CHECK_START_HOUR", "8"))
AI_CHECK_END_HOUR = int(os.environ.get("WEEKLY_STATUS_AI_CHECK_END_HOUR", "12"))

STATE_FILE = Path(os.environ.get("BOT_DATA_DIR", "data")) / "weekly-status-posted.json"
# Send weekly status to the CVE/watchduty-style channel when configured. Fall
# back to the instance webhook so local/staging runs remain non-fatal, but prod
# should set WEEKLY_STATUS_SLACK_WEBHOOK_URL to the CVE-channel webhook.
SLACK_WEBHOOK_URL = os.environ.get("WEEKLY_STATUS_SLACK_WEBHOOK_URL") or os.environ.get("SLACK_WEBHOOK_URL", "")

DEFAULT_REPORTS = [
    {
        "id": "advisor",
        "title": "Advisor / Lightspeed Advisor weekly status",
        "path_template": "reports/{year}/{date}/mpex/lightspeed-advisor.md",
        "search_hint": "advisor|lightspeed|mpex",
    },
    {
        "id": "mpex",
        "title": "MPEX weekly status rollup",
        "path_template": "reports/{year}/{date}/mpex.md",
        "search_hint": "mpex",
    },
]


def _output(status: str, content: str) -> None:
    print(json.dumps({"status": status, "content": content}))


def _now_local() -> datetime:
    return datetime.now(ZoneInfo(TIMEZONE))


def _target_friday(today: date) -> date:
    """Return the Friday report date for the current Friday/Monday cycle."""
    weekday = today.weekday()
    if weekday == AI_CHECK_WEEKDAY and AI_CHECK_WEEKDAY < POLL_WEEKDAY:
        return today - timedelta(days=(7 - POLL_WEEKDAY + AI_CHECK_WEEKDAY))
    if weekday <= POLL_WEEKDAY:
        return today + timedelta(days=(POLL_WEEKDAY - weekday))
    return today - timedelta(days=(weekday - POLL_WEEKDAY))


def _in_hour_window(now: datetime, weekday: int, start_hour: int, end_hour: int) -> bool:
    if now.weekday() != weekday:
        return False
    current = time(now.hour, now.minute)
    return time(start_hour, 0) <= current < time(end_hour, 0)


def _in_watch_window(now: datetime) -> bool:
    return _in_hour_window(now, POLL_WEEKDAY, POLL_START_HOUR, POLL_END_HOUR) or _ai_check_due(now)


def _ai_check_due(now: datetime) -> bool:
    return _in_hour_window(now, AI_CHECK_WEEKDAY, AI_CHECK_START_HOUR, AI_CHECK_END_HOUR)


def _load_reports() -> list[dict]:
    """Load report definitions from env or use Advisor + MPEX defaults.

    WEEKLY_STATUS_REPORTS_JSON can override the defaults with a JSON array of:
      {"id": "...", "title": "...", "path_template": "reports/{year}/{date}/...md", "search_hint": "..."}
    """
    raw = os.environ.get("WEEKLY_STATUS_REPORTS_JSON")
    if not raw:
        return DEFAULT_REPORTS
    try:
        reports = json.loads(raw)
        if isinstance(reports, list) and reports:
            return reports
    except json.JSONDecodeError as exc:
        print(f"WARN: invalid WEEKLY_STATUS_REPORTS_JSON, using defaults: {exc}", file=sys.stderr)
    return DEFAULT_REPORTS


def _report_path(report: dict, target: date) -> str:
    template = report["path_template"]
    return template.format(year=f"{target:%Y}", date=f"{target:%Y-%m-%d}")


def _state_key(report: dict, target: date, path: str) -> str:
    return f"{target:%G-W%V}:{report['id']}:{path}"


def _gh_api(args: list[str], *, raw: bool = False) -> subprocess.CompletedProcess[str]:
    cmd = ["gh", "api", *args]
    if raw:
        cmd.extend(["-H", "Accept: application/vnd.github.raw+json"])
    return subprocess.run(cmd, capture_output=True, text=True, timeout=30)


def _fetch_report_metadata(path: str) -> dict | None:
    proc = _gh_api([f"repos/{REPORT_REPO}/contents/{path}?ref={REPORT_REF}"])
    if proc.returncode != 0:
        stderr = proc.stderr.strip().replace("\n", " ")[:200]
        print(f"Report not available yet: {path} ({stderr})", file=sys.stderr)
        return None
    try:
        return json.loads(proc.stdout)
    except json.JSONDecodeError as exc:
        print(f"WARN: could not parse GitHub metadata for {path}: {exc}", file=sys.stderr)
        return None


def _fetch_report_content(path: str) -> str:
    proc = _gh_api([f"repos/{REPORT_REPO}/contents/{path}?ref={REPORT_REF}"], raw=True)
    if proc.returncode != 0:
        print(f"WARN: could not fetch raw report content: {proc.stderr[:200]}", file=sys.stderr)
        return ""
    return proc.stdout.strip()


def _load_state() -> dict:
    try:
        if STATE_FILE.exists():
            return json.loads(STATE_FILE.read_text())
    except Exception as exc:
        print(f"WARN: could not read weekly status state: {exc}", file=sys.stderr)
    return {}


def _save_state(state: dict) -> None:
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    STATE_FILE.write_text(json.dumps(state, indent=2, sort_keys=True))


def _slack_safe_excerpt(content: str, limit: int = 2000) -> str:
    """Keep the Slack post compact while preserving the manager-authored text."""
    if not content:
        return ""

    # Slack mrkdwn accepts most Markdown-ish text, but headings read better as bold.
    lines = []
    for line in content.splitlines():
        if line.startswith("# "):
            lines.append(f"*{line[2:].strip()}*")
        elif line.startswith("## "):
            lines.append(f"*{line[3:].strip()}*")
        else:
            lines.append(line)

    text = "\n".join(lines).strip()
    if len(text) <= limit:
        return text
    return text[:limit].rstrip() + "\n…"


def _mark_path_check_requested(
    state: dict,
    state_key: str,
    now: datetime,
    target: date,
    report: dict,
    expected_path: str,
) -> None:
    existing = state.get(state_key, {})
    existing.update({
        "posted": False,
        "path_check_requested": True,
        "closed": True,
        "path_check_requested_at": now.isoformat(),
        "repo": REPORT_REPO,
        "ref": REPORT_REF,
        "report_id": report["id"],
        "title": report["title"],
        "path": expected_path,
        "target_date": f"{target:%Y-%m-%d}",
        "reason": "expected report missing at Monday AI fallback window",
    })
    state[state_key] = existing


def _missing_report_prompt(now: datetime, target: date, missing: list[dict]) -> str:
    lines = [
        "## Weekly status path check",
        "",
        "One or more expected weekly status files were not found during the Monday fallback window.",
        "The preflight has already marked these weekly watchers complete before starting this AI session, so it will not search every hour if no file is found.",
        "",
        f"- Current time: {now.isoformat()}",
        f"- Repo: `{REPORT_REPO}`",
        f"- Ref: `{REPORT_REF}`",
        f"- Expected Friday folder date: `{target:%Y-%m-%d}`",
        f"- Dedup state file: `{STATE_FILE}`",
        "",
        "Missing expected reports:",
    ]

    grep_terms = {f"{target:%Y-%m-%d}"}
    for item in missing:
        report = item["report"]
        expected_path = item["path"]
        expected_url = f"https://github.com/{REPORT_REPO}/blob/{REPORT_REF}/{expected_path}"
        grep_terms.update(str(report.get("search_hint", "")).split("|"))
        lines.extend([
            f"- {report['title']} (`{report['id']}`)",
            f"  - Expected path: `{expected_path}`",
            f"  - Expected URL: {expected_url}",
            f"  - Dedup state key: `{item['state_key']}`",
        ])

    grep_expr = "|".join(sorted(t for t in grep_terms if t))
    lines.extend([
        "",
        "Please use GitHub CLI/API to check whether these reports exist under different paths, date folders, team folders, or filenames. Usual paths are:",
        "",
        "`reports/<year>/<friday-date>/mpex/lightspeed-advisor.md`",
        "`reports/<year>/<friday-date>/mpex.md`",
        "",
        "Useful checks:",
        "",
        "```bash",
        f"gh api repos/{REPORT_REPO}/contents/reports/{target:%Y}/{target:%Y-%m-%d}?ref={REPORT_REF} --jq '.[].path'",
        f"gh api repos/{REPORT_REPO}/contents/reports/{target:%Y}/{target:%Y-%m-%d}/mpex?ref={REPORT_REF} --jq '.[].path'",
        f"gh api repos/{REPORT_REPO}/git/trees/{REPORT_REF}?recursive=1 \\",
        f"  --jq '.tree[].path' | grep -Ei '({grep_expr})'",
        "```",
        "",
        "If you find a missing report in a moved/renamed/misdated location, announce it in Slack with a clear note that the expected path changed, include the actual link, and update the matching state key with the actual path/url and outcome.",
        "",
        "Use the CVE/status Slack destination. If `WEEKLY_STATUS_SLACK_WEBHOOK_URL` is set, override `SLACK_WEBHOOK_URL` when calling the slack-notify skill:",
        "",
        "```bash",
        "SLACK_WEBHOOK_URL=\"${WEEKLY_STATUS_SLACK_WEBHOOK_URL:-$SLACK_WEBHOOK_URL}\" \\",
        "  python3 .claude/skills/slack-notify/slack_notify.py \\",
        "  \"weekly-status-YYYY-Www\" \"infra_error\" \"<message>\" 2>&1",
        "```",
        "",
        "If you do not find any likely missing report, do not send a Slack message just for absence unless a human explicitly asked for absence notifications. It is acceptable for this weekly check to end without success.",
    ])
    return "\n".join(lines)


def _send_slack(report: dict, target: date, path: str, html_url: str, content: str) -> bool:
    if not SLACK_WEBHOOK_URL:
        print("WARN: SLACK_WEBHOOK_URL not set, cannot announce weekly status", file=sys.stderr)
        return False

    excerpt = _slack_safe_excerpt(content)
    lines = [
        f"🗓️ *{report['title']} is available* — {target:%Y-%m-%d}",
        f"<{html_url}|{path}>",
    ]
    if excerpt:
        lines.extend(["", excerpt])

    message = "\n".join(lines)
    try:
        payload = json.dumps({"text": message}).encode()
        req = urllib.request.Request(
            SLACK_WEBHOOK_URL,
            data=payload,
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            resp.read()
        return True
    except Exception as exc:
        print(f"WARN: Slack webhook failed for weekly status: {exc}", file=sys.stderr)
        return False


def main() -> None:
    now = _now_local()
    if not _in_watch_window(now):
        _output("skip", "Weekly status watcher outside Friday/Monday publication windows")
        return

    target = _target_friday(now.date())
    state = _load_state()
    reports = _load_reports()
    missing_for_ai = []
    posted = []
    waiting = []

    for report in reports:
        path = _report_path(report, target)
        key = _state_key(report, target, path)
        already = state.get(key, {})
        if already.get("closed") and not already.get("posted"):
            continue

        metadata = _fetch_report_metadata(path)
        if not metadata:
            if _ai_check_due(now):
                _mark_path_check_requested(state, key, now, target, report, path)
                missing_for_ai.append({"report": report, "path": path, "state_key": key})
            else:
                waiting.append(path)
            continue

        sha = metadata.get("sha", "")
        if already.get("sha") == sha and already.get("posted"):
            continue

        html_url = metadata.get("html_url") or f"https://github.com/{REPORT_REPO}/blob/{REPORT_REF}/{path}"
        content = _fetch_report_content(path)
        if not _send_slack(report, target, path, html_url, content):
            waiting.append(f"{path} (Slack announcement failed)")
            continue

        state[key] = {
            "posted": True,
            "closed": True,
            "posted_at": now.isoformat(),
            "repo": REPORT_REPO,
            "ref": REPORT_REF,
            "report_id": report["id"],
            "title": report["title"],
            "path": path,
            "sha": sha,
            "url": html_url,
            "target_date": f"{target:%Y-%m-%d}",
        }
        posted.append(path)
        print(f"Announced {report['id']} weekly status {path} ({sha})", file=sys.stderr)

    _save_state(state)

    if missing_for_ai:
        _output("start", _missing_report_prompt(now, target, missing_for_ai))
        return

    if posted:
        _output("skip", f"Announced weekly status report(s) for {target:%Y-%m-%d}: {', '.join(posted)}")
        return

    if waiting:
        _output("skip", f"Weekly status report(s) not present yet for {target:%Y-%m-%d}: {', '.join(waiting)}")
        return

    _output("skip", f"Weekly status checks already complete for {target:%Y-%m-%d}")


if __name__ == "__main__":
    main()
