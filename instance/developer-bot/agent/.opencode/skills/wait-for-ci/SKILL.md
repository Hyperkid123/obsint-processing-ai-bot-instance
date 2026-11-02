---
name: wait-for-ci
description: >-
  Check CI before CVE review notifications for GitHub application PRs.
  Defer running CI to Rehor's next cycle;
  request manual review with a warning when CI cannot be verified.
---

# Wait for CI

Use for GitHub PRs after creation, pushes, reruns, and before review reminders.
For GitLab, send app-interface's `production-update` or rules-containers'
`rules-image-mr` message immediately through the CVE workflow, including
`<!subteam^S043UGRST2L>`, without this gate or a CI-unverified fallback.
These notices do not claim CI passed; repository CI and human review still apply.

The helper uses Rehor's proxy-backed `gh` client; no tokens or login are needed.
It checks the current commit and confirms success twice, 30 seconds apart. Pending CI
returns immediately: Rehor schedules the next check, without a long-running poll.
Completed `SKIPPED` and `NEUTRAL` checks count as passing, including known expected checks.
Every reported and expected check must be present with `SUCCESS`, `SKIPPED`, or `NEUTRAL`.

## Schedule with Rehor

Use `task_update` metadata and the instance preflight; do not create a separate
scheduler or sleep for a retry cooldown. Store independent deadlines in
`ci_review_pending`: `{ "<PR-URL>": <next-check Unix time> }`.

1. At creation, push, or rerun, set that URL's deadline to now + 1800 seconds
   once. This gives newly started CI time to appear. Preserve other URLs.
2. Before each check, read its saved deadline. A **due recheck** means this URL
   was already pending and that time has arrived; remember this before updating.
   Register an untracked URL with now + 1800 seconds.
3. After a due check, schedule this URL for now + 1800 seconds if still pending.
   Earlier checks preserve its deadline. Never postpone a different request.

If older metadata contains a URL list and `ci_review_next_check`, migrate it to
the map by assigning that saved time to each URL. The preflight accepts both.
A GitLab MR in this map is only awaiting notification delivery: use the
workflow's immediate `production-update` path for app-interface or
`rules-image-mr` for rules-containers, without invoking this CI checker.

Keep deferred/unverified requests pending. Remove a URL after its verified
notification or final failure alert is delivered, or it closes/merges. Persist
retry counts through Rehor's existing task metadata and the CVE workflow policy.

## Check and send

Use the full upstream URL. Add `--expect-check "<NAME>"` for each known expected
check from repository CI configuration. The helper cannot detect an unlisted
check that has not appeared yet; the confirmation delay alone is insufficient.

Compose the workflow's passing message and run the checker and send together:

```bash
python3 "/home/botuser/app/data/remote-config/${BOT_CONFIG_PATH}/agent/.opencode/skills/wait-for-ci/scripts/wait_for_ci.py" "<PR-URL>" && \
SLACK_NOTIFY_MODE=immediate \
  python3 /home/botuser/app/.claude/skills/slack-notify/slack_notify.py \
    "<JIRA-KEY>:watchduty:pr-ready:<RESOURCE-ID>" \
    "<EVENT>" "<MESSAGE>"
```

Use event `pr_created` (or `review_reminder` for a reminder) and resource ID
`<OWNER/REPO>#<NUMBER>`.
Only exit 0 permits the passing send. Read the helper's JSON `review_action`:

- **`ready`**: CI passed; the guarded command can send. Record delivery only
  after the Slack wrapper reports `sent: true`, using the workflow's tracking.
- **`investigate_failure`**: use the workflow's bounded retry/fix policy and final
  failure alert. Schedule cooldowns through Rehor; never call a known failure
  unverified or count pending CI as a failed retry.
- **`defer`**: leave running/changing CI for the next cycle. If `waiting_on` is
  `missing`, also defer initially; on a due recheck, send the unverified fallback.
  Never use this fallback for confirmed running CI.
- **`review_unverified`**: send the fallback now, with the actual reason.
- **`none`**: closed/merged; clear the pending URL without requesting review.

## Fallback when CI cannot be verified

If the helper is missing, crashes, or returns no usable JSON, follow this section
directly. Use the known request URL; never request review for a known closed or
merged request. If its state is unavailable, include that in the explanation.

Send the workflow's `ci-unverified` template through the same Slack wrapper,
without the CI guard, using key `<JIRA-KEY>:watchduty:ci-unverified:<RESOURCE-ID>`
and event `pr_created`. Include the verification blocker and request manual CI
review before merging. This is a review request, never proof that CI passed.

After `sent: true`, record the resource in `watchduty_ci_unverified_requests` and
send this fallback only once per resource. Keep it separate from
`watchduty_notified_prs` so a later verified-success message is still eligible.
Keep checking on scheduled cycles. Failed Slack sends stay retryable; record a
delivery blocker if the wrapper or transport is unavailable.

Use the configured shared webhook with the CVE workflow's WatchDuty mention
and delivery tracking. `/post-pr` still skips Slack; this skill does not
authorize merging, promotion, or ticket closure.
