#!/usr/bin/env python3
"""Pre-flight: watch for the Advisor weekly status report and notify Slack.

The HP weekly status repo uses Friday-dated report folders, e.g.:
  reports/2026/2026-10-30/mpex/lightspeed-advisor.md

Advisor managers are expected to publish on Wednesday at/after 15:00 Prague
for that week's Friday folder. This preflight polls hourly during the watch
window and posts exactly once per report SHA when the file appears.

Exit conditions:
  - Outside watch window -> skip
  - Exact expected report present and new -> send Slack message, persist marker, skip
  - Exact expected report already posted -> skip
  - Exact expected report missing before the final AI check window -> skip and
    keep polling hourly
  - Exact expected report still missing during the final AI check window ->
    start one AI session to search for moved/renamed/misfiled Advisor reports,
    mark this weekly watcher complete, and skip the rest of the week. This is
    intentionally not a hard failure; missing reports are OK, but silent format
    drift is not.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import urllib.request
from datetime import date, datetime, time
from pathlib import Path
from zoneinfo import ZoneInfo

TIMEZONE = os.environ.get("WEEKLY_STATUS_TIMEZONE", "Europe/Prague")
REPORT_REPO = os.environ.get("WEEKLY_STATUS_REPO", "openshift-eng/hp-weekly-status")
REPORT_REF = os.environ.get("WEEKLY_STATUS_REF", "main")
REPORT_RELATIVE_PATH = os.environ.get(
    "WEEKLY_STATUS_RELATIVE_PATH",
    "mpex/lightspeed-advisor.md",
)

# Wednesday 15:00 through Thursday noon Prague by default. The Thursday cutoff
# is intentionally configurable because KEDA/app-interface may keep the bot
# awake for a longer team-specific window.
WATCH_START_WEEKDAY = int(os.environ.get("WEEKLY_STATUS_START_WEEKDAY", "2"))  # Wed, Monday=0
WATCH_START_HOUR = int(os.environ.get("WEEKLY_STATUS_START_HOUR", "15"))
WATCH_END_WEEKDAY = int(os.environ.get("WEEKLY_STATUS_END_WEEKDAY", "3"))  # Thu
WATCH_END_HOUR = int(os.environ.get("WEEKLY_STATUS_END_HOUR", "12"))
# Run the expensive/AI fallback once near the end of the watch window. Before
# then, a missing exact file only means "not published yet".
AI_CHECK_WEEKDAY = int(os.environ.get("WEEKLY_STATUS_AI_CHECK_WEEKDAY", str(WATCH_END_WEEKDAY)))
AI_CHECK_HOUR = int(os.environ.get("WEEKLY_STATUS_AI_CHECK_HOUR", str(max(WATCH_END_HOUR - 1, 0))))

STATE_FILE = Path(os.environ.get("BOT_DATA_DIR", "data")) / "weekly-status-posted.json"
# Send weekly status to the CVE/watchduty-style channel when configured. Fall
# back to the instance webhook so local/staging runs remain non-fatal, but prod
# should set WEEKLY_STATUS_SLACK_WEBHOOK_URL to the CVE-channel webhook.
SLACK_WEBHOOK_URL = os.environ.get("WEEKLY_STATUS_SLACK_WEBHOOK_URL") or os.environ.get("SLACK_WEBHOOK_URL", "")


def _output(status: str, content: str) -> None:
    print(json.dumps({"status": status, "content": content}))


def _now_local() -> datetime:
    return datetime.now(ZoneInfo(TIMEZONE))


def _target_friday(today: date) -> date:
    """Return the Friday report date for the current Wed/Thu watch window."""
    days_until_friday = 4 - today.weekday()  # Friday is 4
    return today if days_until_friday == 0 else today.fromordinal(today.toordinal() + days_until_friday)


def _in_watch_window(now: datetime) -> bool:
    weekday = now.weekday()
    current = time(now.hour, now.minute)

    if weekday == WATCH_START_WEEKDAY:
        return current >= time(WATCH_START_HOUR, 0)
    if weekday == WATCH_END_WEEKDAY:
        return current < time(WATCH_END_HOUR, 0)
    return False


def _report_path(target: date) -> str:
    return f"reports/{target:%Y}/{target:%Y-%m-%d}/{REPORT_RELATIVE_PATH}"


def _ai_check_due(now: datetime) -> bool:
    return (now.weekday(), time(now.hour, now.minute)) >= (AI_CHECK_WEEKDAY, time(AI_CHECK_HOUR, 0))


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


def _slack_safe_excerpt(content: str, limit: int = 2300) -> str:
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


def _mark_path_check_requested(state: dict, state_key: str, now: datetime, target: date, expected_path: str) -> None:
    existing = state.get(state_key, {})
    existing.update({
        "posted": False,
        "path_check_requested": True,
        "closed": True,
        "path_check_requested_at": now.isoformat(),
        "repo": REPORT_REPO,
        "ref": REPORT_REF,
        "path": expected_path,
        "target_date": f"{target:%Y-%m-%d}",
        "reason": "expected report missing at final AI check window",
    })
    state[state_key] = existing
    _save_state(state)


def _missing_report_prompt(now: datetime, target: date, expected_path: str, state_key: str) -> str:
    expected_url = f"https://github.com/{REPORT_REPO}/blob/{REPORT_REF}/{expected_path}"
    return f"""## Advisor weekly status path check

The expected Advisor weekly status file was not found during the publication watch window.

- Current time: {now.isoformat()}
- Repo: `{REPORT_REPO}`
- Ref: `{REPORT_REF}`
- Expected Friday folder date: `{target:%Y-%m-%d}`
- Expected path: `{expected_path}`
- Expected URL: {expected_url}
- Dedup state file: `{STATE_FILE}`
- Dedup state key: `{state_key}`

The preflight has already marked this weekly watcher complete before starting this AI session so it will not search every hour if no file is found. If you find a moved/renamed report and announce it, update the same state key with the actual path/url and outcome.

Please use GitHub CLI/API to check whether the Advisor/Lightspeed Advisor status report exists under a different path, date folder, team folder, or filename in `{REPORT_REPO}`. The usual format is:

`reports/<year>/<friday-date>/mpex/lightspeed-advisor.md`

Useful checks:

```bash
gh api repos/{REPORT_REPO}/contents/reports/{target:%Y}?ref={REPORT_REF}
gh api repos/{REPORT_REPO}/git/trees/{REPORT_REF}?recursive=1 \\
  --jq '.tree[].path' | grep -Ei '({target:%Y-%m-%d}|advisor|lightspeed|mpex)'
```

If you find the Advisor status in a moved/renamed/misdated location, announce it in Slack with a clear note that the expected path changed, include the actual link, and update the existing state key with the actual path/url and outcome.

If you do not find any likely Advisor status file, do not send a Slack message just for absence unless a human explicitly asked for absence notifications. The preflight already marked this weekly check closed; it is acceptable for this weekly check to end without success.
""".strip()


def _send_slack(target: date, path: str, html_url: str, content: str) -> bool:
    if not SLACK_WEBHOOK_URL:
        print("WARN: SLACK_WEBHOOK_URL not set, cannot announce weekly status", file=sys.stderr)
        return False

    excerpt = _slack_safe_excerpt(content)
    lines = [
        f"🗓️ *Advisor weekly status update is available* — {target:%Y-%m-%d}",
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
        _output("skip", "Weekly status watcher outside Wednesday/Thursday publication window")
        return

    target = _target_friday(now.date())
    path = _report_path(target)
    state_key = f"{target:%G-W%V}:{path}"
    state = _load_state()
    already = state.get(state_key, {})
    if already.get("closed") and not already.get("posted"):
        _output("skip", f"Advisor weekly status check already closed for {target:%Y-%m-%d}")
        return

    metadata = _fetch_report_metadata(path)
    if not metadata:
        if not _ai_check_due(now):
            _output("skip", f"Advisor weekly status not present yet before final AI check: {path}")
            return
        _mark_path_check_requested(state, state_key, now, target, path)
        _output("start", _missing_report_prompt(now, target, path, state_key))
        return

    sha = metadata.get("sha", "")
    html_url = metadata.get("html_url") or f"https://github.com/{REPORT_REPO}/blob/{REPORT_REF}/{path}"
    if already.get("sha") == sha and already.get("posted"):
        _output("skip", f"Advisor weekly status already announced for {target:%Y-%m-%d}")
        return

    content = _fetch_report_content(path)
    if not _send_slack(target, path, html_url, content):
        _output("skip", f"Advisor weekly status found but Slack announcement failed: {path}")
        return

    state[state_key] = {
        "posted": True,
        "posted_at": now.isoformat(),
        "repo": REPORT_REPO,
        "ref": REPORT_REF,
        "path": path,
        "sha": sha,
        "url": html_url,
    }
    _save_state(state)
    print(f"Announced Advisor weekly status {path} ({sha})", file=sys.stderr)
    _output("skip", f"Announced Advisor weekly status for {target:%Y-%m-%d}")


if __name__ == "__main__":
    main()
