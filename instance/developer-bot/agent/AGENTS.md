# Obsint Processing Instance — Additional Instructions

## OpenCode coordinator and specialists

`opencode.json` selects `developer-coordinator`. The coordinator and specialists
use GPT-6.1 Sol through the OpenAI Responses proxy, including GitHub, Jira,
GlitchTip, and private GitLab work. Title generation uses GPT-6 Luna through the
same provider. Keep the existing bot identity and task ownership throughout each workflow.

Select the matching native skill and specialist for each task:

- CVE: load `/cve-workflow`, delegate technical phases to `cve`.
- GlitchTip: load `/glitchtip-workflow`, delegate technical phases to `glitchtip`.
- Go backend: load `/golang-workflow`, delegate technical phases to `golang`.
- Python backend: load `/python-backend-workflow`, delegate technical phases to `python-backend`.
- Frontend maintenance: load `/frontend-maintenance-workflow`, delegate technical phases to `frontend-maintenance`.

For productized rules image CVEs, apply `/cve-workflow`'s repository preparation
override **before** generic checkout preparation or retries: fetch upstream
`public` for OCP or `main` for ACM, preserving the configured fork/upstream
roles. Never prepare these tasks using the generic `master` reset.

Load the matching workflow skill before delegating and again before lifecycle
actions after resuming a task. CVE and GlitchTip workflows take precedence over
generic language workflows. When shared instructions refer to a legacy persona,
use the matching native workflow skill and specialist above; do not read legacy
`personas/` paths. The coordinator owns Jira/task updates, commits, pushes,
PR/MR lifecycle, notifications, deployment checks, and cycle completion.
Specialists investigate, change local files, and test only their assigned phase;
inspect their changes and evidence before acting. Delegate sequentially when
specialists would share a checkout. Maximum delegation depth is one.

Use `/home/botuser/repos` for checkouts, `/home/botuser/data` for persistent task
state, and `/tmp/opencode` for scratch. The runner workspace is
`/home/botuser/app`. Custom helper commands below use the current synced profile
under `/home/botuser/app/data/remote-config/${BOT_CONFIG_PATH}/agent/.opencode/skills`;
`BOT_CONFIG_PATH` must identify this profile. Shared runtime helpers are still
installed under `/home/botuser/app/.claude/skills` by the pinned runner; that is
an operational script path, not Claude instruction discovery.

OpenCode V2 has no LSP runtime. Use repository lint, typecheck/compiler, and test
commands for diagnostics; never claim LSP verification. Browser verification
still requires the configured browser tools to be available.

## Slack Notifications — Shared Destination

All developer-bot notifications use the configured `SLACK_WEBHOOK_URL` Incoming
Webhook. The same destination receives routine updates and CVE alerts. The
coordinator sends notifications; specialists do not send their own copies.
These rules override inherited plain-text notification examples and automatic
helper messages. CVE timing, mentions, keys, and delivery tracking are defined
in `/cve-workflow` and take precedence over the normal-message rules below.

For **non-CVE** messages, use Slack mrkdwn with an emoji, a bold linked title,
a short summary, and the action needed. For example:

```text
🛠️ *Ready for review: <JIRA-URL|JIRA-KEY>*
*Change:* <one-line summary>
*PR:* <PR-URL|repo#number>
*Next:* Please review the change.
```

Adapt the title to the event: review reminder, help needed, or merged. Include
the Jira link and relevant PR/MR links; for blockers without a PR, link the
affected resource instead. Add brief context or verified test results when
useful. Use actual newlines, not literal `\n`, and keep messages concise.
**Normal notifications must not ping any user, group, or channel**, including
WatchDuty. Treat titles, error text, and summaries as plain text: escape their
`&`, `<`, and `>` characters so copied mention markup cannot trigger a ping.
Construct Slack links separately from verified URLs and labels.

Send through the shared helper with the formatted message as one safely quoted
argument. Never use raw `curl`, direct `slack_notify` MCP calls, or a destination
override:

```bash
SLACK_NOTIFY_MODE=immediate \
  python3 /home/botuser/app/.claude/skills/slack-notify/slack_notify.py \
    "<JIRA-KEY>" "<EVENT-TYPE>" "<FORMATTED-MULTILINE-MESSAGE>"
```

Keep the existing normal event types (`pr_created`, `review_reminder`,
`release_pending`, `needs_help`, `infra_error`) and Jira key so the server's
48-hour cooldown still applies. Inspect the JSON response: only `sent: true`
means delivered; a cooldown is not a new delivery. Missing configuration or a
send failure is reported without exposing the webhook and does not block the
PR lifecycle. Do not bypass cooldowns or try another destination.

Avoid duplicate or unformatted automatic messages:

- For `/post-pr`, run its operations entry point with Slack skipped, then send
  the single formatted `pr_created` notification at the workflow's notification
  step. Do not also send the inherited jira-sprint plain-text example:

  ```bash
  python3 /home/botuser/app/.claude/skills/post-pr/scripts/post_pr_operations.py \
    "<PR-URL>" "<PR-NUMBER>" "<JIRA-KEY>" "<SUMMARY>" --skip slack
  ```

- For non-CVE `/wrap-up`, preserve all production and workflow gates and stage
  the required outcome report, then disable only the helper's automatic send:

  ```bash
  SLACK_WEBHOOK_URL="" python3 /home/botuser/app/.claude/skills/wrap-up/wrap_up.py "<JIRA-KEY>" 2>&1
  ```

  After confirming the Jira transition and archive succeeded, send one formatted
  `release_pending` message through the shared helper. If a step failed, follow
  `/wrap-up` recovery and describe only confirmed results; never announce a
  completed transition before it succeeds. Keep the built-in send disabled for
  recovery invocations too. The empty value is command-local, so the separate
  notification command still uses the configured webhook. CVE wrap-up follows
  `/cve-workflow`'s existing notification-before-archive sequence instead.

## CVE PR Notifications — CI Gate (overrides jira-sprint workflow)

For CVE tickets, reload `/cve-workflow` and use `/wait-for-ci` before any
GitHub application PR-ready notification or review reminder.
Only a fresh passing check may authorize a CI-passed message; completed skipped and neutral
jobs count as passing. `/post-pr` must use
`post_pr_operations.py --skip slack`; skip jira-sprint's immediate Slack message.

The skill uses Rehor's proxy clients, task metadata, and instance preflight to
schedule pending checks and retry cooldowns across cycles. Do not sleep/poll for
30 minutes inside a session. Known failures follow the workflow's bounded retry
policy; running CI waits. Closed/merged requests do not get review requests.

**For GitHub PRs, if the skill/helper is missing, crashes, or cannot verify CI, request review
using the workflow's `ci-unverified` template.** State the blocker and request
manual CI verification; do not require the broken helper to succeed. Deduplicate
this fallback separately from later CI-passed messages. Every CVE notification
uses the shared webhook with the WatchDuty mention and the workflow's delivery
tracking.

**App-interface promotion MRs are exempt from the CI notification gate.** Send
the workflow's `production-update` message immediately after MR creation, with
the WatchDuty mention `<!subteam^S043UGRST2L>`. Do not run `/wait-for-ci` or wait
for GitLab CI, and do not claim its CI passed. Use the same direct path for
undelivered promotion notices from earlier cycles. Human review and the
production-before-closure rule below still apply.

**Rules-containers source MRs also bypass the GitHub notification checker.**
Use `/cve-workflow`'s dedicated `rules-image-mr` notice immediately, including
delivery retries. Do not use the app-interface template or claim the image is
already safe. Human review, required CI, and catalog-publication verification
still apply.

## PR Merged — Promote to Prod BEFORE Closing (overrides jira-sprint workflow)

Overrides the workflow's **PR merged** step. For standard service components, a
repo PR merge only deploys to **stage**; production needs a separate
app-interface GitLab MR. Productized rules images instead require Konflux release
and registry.redhat.io catalog publication, not an app-interface MR. **A CVE
ticket must not be closed/wrapped up until its applicable production gate is
complete.**

When a PR is `MERGED` (Priority 1 "PR merged" and Priority 1.5 triage), before
invoking `/wrap-up`:

For CVE tickets, reload `/cve-workflow` before every `/wrap-up`,
including Priority 1.5. Use its CVE notification and helper invocation rules so
post-merge bookkeeping does not send a duplicate or unformatted Slack message.

- **CVE ticket OR `repo:app-interface` label present** → do NOT `/wrap-up` until
  the applicable production gate is complete:
  - **Productized rules images** (`lightspeed-services-ocp/ocp-rules-rhel9`,
    repo `rules-containers`, and `lightspeed-services-acm/ocp-rules-rhel9`,
    repo `lightspeed-advisor-on-premise-ocp`): these images have no
    app-interface deployment. Never create an app-interface MR for them. Their
    production gate is Konflux release and publication of the fixed image to
    `registry.redhat.io` (see `/cve-workflow`'s "Productized Rules Images"
    section and the resolve-cve `rules-containers-public.md` /
    `insights-on-prem-acm.md` references). For inherited-package ACM CVEs,
    also require the fixed OCP base tag to be referenced by the ACM
    `Dockerfile` `FROM`.
    - While this gate is pending, keep the ticket in "Code Review" and use the
      productized-image `[PROD-GATE:OPEN]` instructions below; record the
      expected image/tag (or "not released yet"). Do not follow the
      app-interface steps below.
    - Once the catalog tag is visible and, for inherited-package ACM CVEs, the
      ACM base-tag condition is satisfied for the published build, verify the
      affected package versions in that published image and record its digest
      and evidence. Include ACM-owned shared pins/copies in the check; a safe
      base alone does not prove the ACM image is safe. Only then clear the gate
      using `[PROD-GATE:CLEARED]` and proceed to `/wrap-up`.
  - **All other CVE tickets and non-CVE `repo:app-interface` tickets** use the
    app-interface production flow:
    - No app-interface MR yet → follow **Production Image Update (app-interface)**
      in `/cve-workflow` (wait for Konflux build → check app-interface
      deployment config → open MR with `glab mr create --repo <app-interface-path>
      --hostname gitlab.cee.redhat.com`). Add attribution note. Transition ticket
      → "Code Review". Task stays `in_progress`. Jira comment with MR link.
    - App-interface MR open, not merged → leave ticket in "Code Review", await
      merge. No `/wrap-up`.
    - App-interface MR merged (production updated) → now invoke `/wrap-up`.
    - App-interface NOT in `agent/project-repos.json` → note in Jira, then
      `/wrap-up`.
- **Otherwise (no prod promotion needed / production already current)** → invoke
  `/wrap-up` as the workflow describes.

**Gate**: while the applicable production gate is pending, the ticket stays
"Code Review", NOT closed. Production is confirmed **before** the ticket is
closed.

### Safeguard — Self-Ping Marker Comment

The gate above is instruction-only. Reinforce it with a durable, self-recognizable
marker posted to the Jira ticket, so future cycles re-read it and cannot forget.

**When the gate first activates** (PR merged, production update still pending),
post a Jira comment (`jira_add_comment`) containing the exact marker line below.
For standard app-interface tickets, use the first form; for productized rules
images, use the second form so the catalog tag is tracked instead of an MR URL:

Standard app-interface gate:
```
🔒 [PROD-GATE:OPEN] {mention} Do NOT close this ticket. Production promotion pending.
app-interface MR: <MR_URL or "not created yet">
Close/`/wrap-up` is allowed ONLY after this MR is merged and production is updated.
```

Productized rules image gate:
```
🔒 [PROD-GATE:OPEN] {mention} Do NOT close this ticket. Production publication pending.
Image/tag: <registry.redhat.io image + expected tag, or "not released yet">
Close/`/wrap-up` is allowed ONLY after the fixed tag is visible in the catalog.
Record the published digest and package verification evidence before clearing this gate.
For inherited-package ACM CVEs, the published ACM build must also use the fixed OCP base, verified from its Dockerfile/provenance.
```

**Ping the bot itself** via `{mention}` — **only for CVE tickets, where the bot is
the assignee.** Mention the assignee (the bot's own Jira user, `$BOT_JIRA_EMAIL`)
so the reminder notifies the bot on the next cycle. For a non-CVE `repo:app-interface`
ticket the assignee may be a human — do NOT @mention them; drop `{mention}` and post
the marker without a ping (the marker + task metadata still gate the close).

Also record it in task state as backup. For an app-interface gate, use
`task_update` metadata `{"prod_gate": "open", "next_step": "await app-interface MR merge",
"app_interface_mr": "<url>"}`. For a productized rules image, use
`{"prod_gate": "open", "next_step": "await registry.redhat.io catalog publication",
"production_target": "<published image/tag; include fixed OCP base tag for inherited ACM CVEs>"}`.

**Every cycle, before any `/wrap-up` or close/`Release Pending` transition**: read
the ticket's gate markers in chronological order for the current production
target. A later verified `[PROD-GATE:CLEARED]` supersedes an earlier
`[PROD-GATE:OPEN]`; an old open comment alone must not reopen a cleared gate.
Also check task metadata `prod_gate`. If comments and metadata disagree, verify
the production evidence and reconcile them before closing. If the latest gate
is open or the required evidence is missing, the ticket remains gated:
- Do NOT `/wrap-up`, do NOT close. Keep it in "Code Review".
- This marker is a **bot self-assigned task**, NOT resolved feedback — treat it as
  an outstanding action item even though it is a bot-authored comment.

**Clearing the gate** — only after the applicable production target is confirmed:
- For app-interface, the MR is merged and production is confirmed updated.
- For productized rules images, the fixed tag is visible in the registry.redhat.io
  catalog and its published digest/package verification evidence is recorded;
  for inherited-package ACM CVEs (including combined base/ACM-layer fixes),
  the published ACM build's `Dockerfile`/provenance confirms the fixed OCP base
  as well. A change to today's source `FROM` or a clean base scan alone is not
  sufficient.
1. Post a Jira comment with `✅ [PROD-GATE:CLEARED] Production updated — safe to close.`
2. `task_update` metadata `{"prod_gate": "cleared"}`.
3. Then proceed to `/wrap-up`.

Never delete or edit the `[PROD-GATE:OPEN]` comment; clear it only by posting the
`[PROD-GATE:CLEARED]` follow-up. Both markers use these exact strings so they stay
machine-recognizable across cycles regardless of the bot/human content heuristic.

## Version Management

This instance has **nvm** (Node) and **goenv** (Go) version managers installed. Use them to match the version required by each repo.

### Node.js (nvm)

Before working on a repo with `package.json`, check its `.nvmrc` or `engines.node` field and switch if needed:

```bash
nvm use          # reads .nvmrc if present
nvm install 20   # install + switch to Node 20
nvm use default  # back to default (22)
```

Default: Node.js 22 LTS. Available globally via `/usr/local/bin/node`.

### Go (goenv)

Before working on a repo with `go.mod`, check the `go` directive and switch if needed:

```bash
goenv versions                    # list installed versions
goenv install 1.23.0              # install a new version
goenv global 1.23.0               # set as default
goenv local 1.24.2                # set for current directory only
```

Pre-installed: Go 1.24.2 (default), 1.25.7. Available globally via `/usr/local/bin/go`.

### When to switch

- `go.mod` says `go 1.23` → `goenv install 1.23.x && goenv local 1.23.x`
- `.nvmrc` says `20` → `nvm use 20` (installs automatically if missing)
- No version file → use the defaults
