# Obsint HyperShell migration draft

This prepares dispatcher profiles for the three obsint instances (developer
bot, backlog groomer, watchduty). It does not enable the dispatcher or replace
the existing deployments. Do not enable this repository in the dispatcher until
the gates below are complete — this draft is expected to stay open longer than
the other instance migrations because of the proxy and secret dependencies.

## What changes here

- Each profile gains the dispatcher profile layer: sandbox image (`tag:
  latest`, newest promoted Konflux build of main), top-level non-secret `env`
  (instance identity, label, config path, coordinator engine), and declarative
  TypeScript preflights executed by the dispatcher's engine instead of the
  workflow Python sets. The OpenCode/OpenAI migration itself (runtime,
  provider, native AGENTS/subagents/skills) is already on master; this draft
  additionally assigns role-based model tiers on top of it: GPT-6.1 Sol for the
  coordinating primary agents (developer-bot `developer-coordinator`,
  watchduty `watchduty`), GPT-6.1 Terra for the implementing specialists
  (developer-bot `cve`/`glitchtip`/`golang`/`python-backend`/
  `frontend-maintenance`, watchduty `mintmaker-triage`), and GPT-6 Luna for
  bounded utility work (title generation, watchduty `message-composer`, and
  the backlog groomer). Each profile's `opencode-deployment.json` declares
  every selected model under `rehor-openai`, matching the pinned runtime's
  packaged catalog; the team proxy's OpenAI model allowlist already covers
  all three IDs, and whichever gateway the separate-proxy gate selects must
  serve them at cutover.
- Preflight ports live in Rehor (`@rehor/preflights`):
  `obsint-jenkins-status`, `obsint-mintmaker-prs`,
  `obsint-weekly-status-update` (watchduty), `obsint-backlog-scan`,
  `obsint-cve-scan` (groomer), `obsint-cve-ci-review` (developer bot), plus
  the shared `gh-pr-status` / `gl-mr-status` / `jira-sprint` set. The
  workflow manifests keep their Python preflight lists as the legacy rollback
  path.
- Instance IDs, labels, and board settings are copied from the current stage
  GitOps deployment; changing an instance ID hides its existing tasks.

## Before rollout

1. Rehor PR #667 (preflights package), #668 (top-level
   `env`), and the obsint preflights port (#670) are merged; deploy a dispatcher
   image containing them. Older dispatchers ignore top-level `env` and cannot
   run the declared preflights. The `dev-bot` submodule is pinned to merged Rehor
   master `f23332ec` (#683), which carries the env-unit preflight handling,
   native sandbox environment delivery, and Tier 2 `sandbox.env` secret
   rendering.
2. **Separate proxy decision (hard gate).** The obsint bots currently run
   their own team proxy with a different credential set than the shared
   platform services (Glitchtip, registry auth, git-auth, GitLab TLS
   handling). Dispatcher sandboxes reach external systems through the
   services gateway; obsint's credentials must either be added to that
   gateway's secret set or served by a dedicated gateway before cutover.
   Resolve and document this before any dispatcher enablement.
3. **Secret wiring (hard gate, not in this PR).** Deployment-owned Slack
   webhook configuration for all three bots (including the weekly-status
   override channel) must be rendered into dispatcher configuration; Jenkins
   instance endpoints likewise. The sandbox-side Glitchtip credential used by
   the developer-bot skill requires a deployment-provisioned Tier 2 Kubernetes
   Secret plus dispatcher RBAC; the pinned runtime resolves `sandbox.env`
   secret references, but the Secret is deployment-owned and not provisioned
   yet. No secret values, paths, or references belong in this repository.
4. Dispatcher runtime wiring: a writable persistent preflight state directory
   (self-throttle and weekly-marker bookkeeping) and network egress from the
   dispatcher to the Jenkins hosts and the Slack webhook endpoint.
5. Promote a runner image built from the pinned `dev-bot` submodule
   (`f23332ec`) and verify the configured `latest` tag resolves to it. The
   dispatcher checks out the config repository and its pinned submodule
   separately from the baked runner image; their contents must stay compatible.
6. Schedule parity: each profile's `schedule:` declaration now mirrors its
   legacy KEDA window from app-interface (developer-bot Mon-Fri 08:00-18:00
   Prague; backlog-groomer and watchduty Mon-Fri 08:00-16:00 Prague), so the
   dispatcher starts no sessions outside the windows the legacy deployments
   used. The ported preflights keep self-throttling inside them (mintmaker
   8 h, weekly-status Friday/Monday windows, groomer Wednesday gate);
   in-flight sessions drain past a window's end. Confirm the windows still
   match intent before cutover so legacy runners and the dispatcher never
   process the same instance simultaneously.
7. The `jira-sprint` `email` candidate pass is unconfigured (the bot's Jira
   identity is a deployment secret); validate assigned-ticket behavior before
   cutover.
8. Validation cycles: no-work cycle; watchduty new-failure and
   recovery/cleanup cycles including the compact Slack report; mintmaker
   triage and merged-PR cleanup; weekly-status Friday announce and Monday
   AI fallback; groomer Wednesday scan; CVE fixVersion assignment; one
   representative developer-bot integration cycle. Verify task identity,
   memory bookkeeping, and cleanup.

## Rollback

Remove this repository from dispatcher configuration and let in-flight
sessions drain before re-enabling the legacy KEDA deployments. The workflow
manifests, Python preflights, and the legacy deployment templates remain in
this repository unchanged; a config revert restores the pre-migration state
with the currently deployed image.
