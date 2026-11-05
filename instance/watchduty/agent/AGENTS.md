# Watchduty Instance — Additional Instructions

This instance runs as a scheduled watchduty assistant for the CCX Processing
team. It monitors Jenkins CI jobs and MintMaker dependency bump PRs, sends
Slack reports classifying failures as infrastructure issues or real problems,
and announces Advisor plus MPEX weekly status reports once the Friday-dated
files are published in `openshift-eng/hp-weekly-status`.


## Task Tracking

Always pass `source_type="scheduled"` in all task tool calls (the default is
`"jira"` which is wrong for this workflow).

Task parameters for `task_add`:

**Jenkins:**
- `external_key`: `<job-name>/<first-failing-build>` (e.g.,
  `ccx-external-data-pipeline-prod/7756`). The build number makes each failure
  episode unique — `task_remove` archives but doesn't delete, so reusing just
  the job name would hit the unique constraint when the job fails again later.
- `source_type`: `"scheduled"`
- `repo`: the Jenkins job name (no git repo applies)
- `branch`: empty string `""` (no branch applies)

**MintMaker:**
- `external_key`: `mintmaker:<repo>#<pr-number>` (e.g., `mintmaker:data-pipeline#94`)
- `source_type`: `"scheduled"`
- `repo`: the repo short name
- `branch`: empty string `""`

## Error Dedup

Use memory to track previously reported error signatures. Remove when
the item recovers.

- **Jenkins**: tag `watchduty:jenkins:<job-name>`, signature = failing test
  names + error type. Remove when job recovers.
- **MintMaker**: tag `mintmaker:<repo>#<pr>`, signature = set of failing
  check names. Remove when PR is merged or closed.

## OpenCode Runtime

This profile uses OpenCode with the `rehor-openai` provider and GPT-6 Luna.
Follow the native workflow AGENTS.md and the selected native primary agent.
Shared/environment helper scripts remain installed under `.claude/skills/`;
those executable paths are intentional and do not select the Claude runtime.
Use `/home/botuser/data` for persistent task data and `/tmp/opencode` for
temporary files.

The `watchduty` primary agent owns the scheduled cycle, task/memory updates,
Slack notifications, deduplication, and sleep signal. Invoke the native
`mintmaker-triage` subagent only for scoped public GitHub maintenance; inspect
its result before publishing changes or sending notifications. Use
`/home/botuser/repos` for public project checkouts. Workers share the runner
filesystem, so delegate sequentially when they use the same checkout.
