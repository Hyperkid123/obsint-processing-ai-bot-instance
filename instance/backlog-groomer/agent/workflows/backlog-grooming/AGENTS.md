Backlog grooming bot. Assess Jira backlog ticket quality and groom CVEs.

**Schedule**: CVE grooming runs every hour; backlog grooming runs once per week
on Wednesday morning. The preflights self-throttle via dedup markers so a single
hourly cycle handles both.

**SLEEP BETWEEN RUNS**: After completing step 4, write `data/cycle-sleep.json`
with `{"recommended_sleep": 3600, "reason": "grooming_complete"}` so the
runner sleeps ~1h before the next cycle (CVE scan runs hourly, backlog scan
self-throttles to once per week via its own dedup marker).

## Workflow

Single-pass grooming cycle.

**Status updates** via `bot_status_update`:
- Cycle start: `working`, "Starting grooming cycle..."
- Cycle end: `idle`, "Grooming complete. Sleeping..."
- Error: `error`, "<what went wrong>"

### Step 0: Create batch task (or detect prior run)

First, check if this cycle already ran: call `task_get` with
`external_key`: `grooming-YYYY-MM-DD-HH` (date + current hour),
`source_type`: `scheduled`.
If it exists and status is `done`, write `data/cycle-sleep.json` with
`{"recommended_sleep": 3600, "reason": "already_groomed_this_cycle"}` and exit.
If it exists and status is `in_progress`, resume from where it left off
(check `metadata.last_step`).

Otherwise create a new task using `task_add`:
- `title`: "Grooming batch — N tickets"
- `external_key`: `grooming-YYYY-MM-DD-HH` (date + current hour, e.g. `grooming-2026-07-14-08`)
- `source_type`: `scheduled`
- `status`: `in_progress`

Then `task_update` with `summary`: "Starting grooming cycle — N tickets", `metadata`: `{"last_step": "starting", "next_step": "assessing_tickets"}`.

This tracks the cycle in the dashboard.
Do NOT create one task per ticket — use one batch task per cycle.

### Step 1: Read pre-fetched ticket data and filter duplicates

The preflight script already fetched backlog tickets (zero token cost).
The data is in your prompt. Do NOT call `jira_search` again.

**Skip already-assessed backlog tickets**: call `memory_search` with query
"groomed tickets" to find prior grooming memories. Filter out any ticket
keys that appear in previous backlog assessments. This filter applies only
to Step 2, not to the separate `## CVE Grooming` section: those CVEs still
need Step 1.5 even if they appeared in a previous assessment.

Exit only if neither new backlog tickets nor CVE grooming tickets remain.
An empty/skipped weekly backlog scan must not skip hourly CVE grooming.
If both are empty, mark the batch task `done` with `summary`:
"No new tickets to groom", write `data/cycle-sleep.json` with
`{"recommended_sleep": 3600, "reason": "no_new_tickets"}`, and exit.

### Step 1.5: CVE Grooming (auto-labeling)

Check if your prompt contains a `## CVE Grooming` section (from the
`02-cve-scan.py` preflight). If it does NOT exist, skip to Step 2.

**This step is live.** Its CVE labels, story points, and sprint updates are
authorized independently of the backlog-assessment dry-run in Step 3.

For each CVE ticket in the section:

1. **Extract component name** from the summary. CVE summaries follow the
   pattern `CVE-YYYY-NNNNN {Component}: {Package}: {Title}`. The component
   is the first token after the CVE ID (e.g., in
   `CVE-2026-13676 aggregator: golang: ...`, the component is `aggregator`).
   The component can be a full image name containing slashes (e.g.
   `CVE-2026-96636 lightspeed-services-ocp/ocp-rules-rhel9: pillow: ...` —
   the component is `lightspeed-services-ocp/ocp-rules-rhel9`; do not split
   it on `/`).

2. **Map component to repo** using the mapping table in your native
   `backlog-grooming` agent instructions (section "CVE Component
   to Repo Mapping"). Cross-reference against `project-repos.json` to
   confirm the repo key is valid. `app-interface` and `rules-containers` are
   known exceptions — they are valid fixed mappings (private GitLab repos)
   that are intentionally absent from this profile's `project-repos.json`;
   do not flag them as unknown. If no match, mark as "unknown component".

3. **Determine labels to assign**:
   - `obsint-processing-ai` (bot pickup label)
   - `repo:<matched-repo-name>` (component repo)
   - `repo:app-interface` for standard CVEs whose production image is updated
     through app-interface.

   **Exception — productized rules images**: match case-insensitively and
   apply the ACM rule first. If the component contains `lightspeed-services-acm`,
   assign only `obsint-processing-ai` and
   `repo:lightspeed-advisor-on-premise-ocp`. Otherwise, if it contains
   `lightspeed-services-ocp` or `ocp-rules-rhel9`, assign only
   `obsint-processing-ai` and `repo:rules-containers`. Do NOT add
   `repo:app-interface` — these images are not deployed via app-interface;
   their production update is the registry.redhat.io catalog publication via
   the Konflux release flow.

4. For each ticket, call `jira_update_issue` to ADD exactly the labels selected
   in Step 3. Do not add `repo:app-interface` to a productized rules image
   ticket.

   Do NOT remove existing labels. Only add new ones.

4b. **Quarterly fixVersion**: The `02-cve-scan.py` preflight already
   assigns the current quarter as a fixVersion (e.g. `2026Q3`, derived
   from today's date) to any CVE ticket that has no `YYYYQ[1-4]`
   fixVersion yet — existing fixVersions are preserved and an
   already-planned quarter is never overwritten. No action needed here;
   note the assigned quarter (shown in the preflight `fixVersions` line)
   in the report.

5. **Set story points**: If the ticket has no story points
   (`customfield_10028` is null), set it to `3` via `jira_update_issue`
   with `{"customfield_10028": 3}`. Use the preflight's `story_points` value;
   preserve existing estimates, including `0`. If it is `unknown`, call
   `jira_get_issue` for `customfield_10028,customfield_10020` first. Missing
   data or a failed lookup does not authorize overwriting either field.

6. **Add to active sprint**: Read the preflight's `sprints` value; preserve
   any active or future sprint assignment. If the value is missing/unknown
   or does not expose sprint state, fetch/verify it before making a change.
   If there is no active/future sprint (null, empty, or only closed sprints),
   find the active sprint on board `1553` (`CCX Core - Processing`)
   using `jira_get_sprints_from_board` with `state: "active"`, then
   call `jira_add_issues_to_sprint` to add the ticket.

   Include the results in `task_update` metadata:

   ```
   task_update metadata.cve_grooming: [
     {"key": "CCXDEV-XXXXX", "component": "aggregator",
      "labels_added": ["obsint-processing-ai", "repo:insights-results-aggregator", "repo:app-interface"],
      "story_points_set": true, "sprint_added": "CCXDEV Sprint 173",
      "match_confidence": "high"},
     ...
   ]
   ```

   Append a summary line per ticket:
   ```
   CVE: CCXDEV-XXXXX (aggregator) → +labels +3sp +sprint
   ```

If a component cannot be matched, include it in the report with
`"match_confidence": "unknown"` and note it needs manual mapping.

After processing all CVE tickets, update the batch task:
`task_update` with `summary`: append CVE grooming results,
`metadata`: add `cve_grooming` array with per-ticket results.

### Step 2: Assess each ticket

Use the team context in your native `backlog-grooming` agent instructions,
then evaluate each ticket for:

<!-- Basic criteria — more refined assessment rules in CCXDEV-16532 -->
1. **Clarity** — clear enough to start work? Steps to reproduce for bugs?
2. **Scope** — appropriately sized for one sprint? Should it be split?
3. **Context** — can the affected repo be identified? Is there a `repo:` label?
4. **Priority** — is it set (not "Undefined")?
5. **Staleness** — older than 6 months with no activity?
6. **Epic health** — the preflight includes parent/epic info. If the epic is
   closed (Done, Closed, or resolution like Duplicate/Won't Do), recommend
   closing the child task too with the reason (e.g. "Epic CCXDEV-XXXXX closed
   as Duplicate").
7. **Quarter planning** — the preflight includes targetQuarter and fixVersions.
   When recommending "Ready for sprint", note whether the ticket is planned for
   the current quarter. If it has no quarter and no fixVersion, flag it as
   "Ready but unplanned — needs quarter assignment".

Every 3 tickets (or after the last one), call `task_update` with
`summary`: "Assessed M/N tickets", `metadata`: `{"last_step": "assessing_tickets", "tickets_assessed": M}`.

If an error occurs, call `task_update` with `paused_reason`: "<what went wrong>"
— do NOT set `status: done`, leave as `in_progress` so the dashboard shows it's stuck.

### Step 3: Report results (DRY-RUN MODE)

**Backlog assessment is in DRY-RUN.** Do not post assessment comments or add
`ai-groomed` labels. This restriction applies only to Step 3; the live CVE
updates in Step 1.5 still run, even when no backlog tickets need assessment.

Build a `results` list with one entry per ticket:

```
CCXDEV-XXXXX: [Ready / Needs refinement / Consider closing] — <one-line reason>
```

Then call `task_update` with:
- `summary`: the full results list joined by newlines (this IS the grooming output — the dashboard is the only place to see it in dry-run mode)
- `metadata`:
  ```json
  {
    "last_step": "report_complete",
    "next_step": "grooming_complete",
    "results": [
      {"key": "CCXDEV-XXXXX", "verdict": "Ready", "clarity": "Good", "scope": "Appropriate", "component": "repo-name", "suggestion": "..."},
      ...
    ]
  }
  ```

Do NOT call `jira_add_comment` or `jira_update_issue` for Step 3's assessments.
Preserve the CVE grooming results in the batch summary when reporting them
alongside backlog assessments or when the backlog section is empty.

<!-- LIVE MODE — delete the DRY-RUN block above and uncomment this:

For each ticket, post a Jira comment using `jira_add_comment` with the
assessment. Append footer:

```
---
_Automated grooming by backlog-groomer bot_
```

Then add `ai-groomed` label via `jira_update_issue`.

Also call `task_update` with the summary + results metadata as above.

END LIVE MODE -->

### Step 4: Wrap up

Five calls, all required:

1. `task_update` with `status: done`, `summary`: keep the per-ticket results from step 3 (do NOT replace with a generic count),
   `metadata`: `{"last_step": "grooming_complete", "tickets_assessed": N}`
2. `progress_store` with `task_id`: batch task ID, `instance_id`: your instance ID,
   `cycle_type`: `task_work`, `progress`: `{"last_step": "grooming_complete", "summary": "<first 200 chars of summary>", "tickets_assessed": N}`
3. `memory_store` with content: "Groomed tickets on YYYY-MM-DD: CCXDEV-111, CCXDEV-222, ..."
   and tags: `["grooming", "groomed-tickets"]`. This lets future cycles skip already-groomed tickets
   (needed in dry-run mode since ai-groomed labels are not added to Jira).
4. `bot_status_update` with `state: idle`, message: "Grooming complete. Sleeping..."
5. Write `data/cycle-sleep.json` with `{"recommended_sleep": 3600, "reason": "grooming_complete"}`

Do NOT skip `status: done` on `task_update` — without it the dashboard shows the task stuck as in-progress.

## Constraints

- Do NOT transition ticket status
- Do NOT assign tickets
- Process at most 10 tickets per cycle
