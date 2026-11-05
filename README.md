# obsint-processing-ai-bot-instance

Three [Řehoř](https://github.com/OpenShift-Fleet/rehor) bot profiles serve the CCX
Processing team: developer-bot implements Jira work, WatchDuty monitors CI and
MintMaker PRs, and backlog-groomer assesses Jira tickets and prepares CVE work.

For ticket assignment and operating guidance, see [CCX Docs — agents](https://ccx.pages.redhat.com/ccx-docs/docs/processing/agents/).
The shared [dashboard](https://devbot-memory-server-platform-frontend-ai-dev-stage.apps.rosa.hcmais01ue1.s9m2.p3.openshiftapps.com/#/instances)
shows task progress and bot activity.

## Runtime and model selection

All profiles use native OpenCode V2 through the coordinator execution engine.
The `dev-bot` submodule pins the merged runtime at
`58a87cd9dcf0c27ca41f9c48e847794314a1bfda` (OpenCode CLI/client 2.1.2).
This runtime uses a per-cycle USD cost budget (`claude.budgetUsd` in its
`config.json`, default `5`) instead of the previous tool-call limit.

| Profile | Provider and model | Work |
| --- | --- | --- |
| `instance/developer-bot` | OpenAI Responses: GPT-6.1 Sol, medium reasoning; GPT-6 Luna utility model | Jira implementation, public GitHub, private GitLab/app-interface, CVEs, GlitchTip |
| `instance/watchduty` | OpenAI Responses: GPT-6 Luna, medium reasoning | Jenkins monitoring and public GitHub MintMaker maintenance |
| `instance/backlog-groomer` | OpenAI Responses: GPT-6 Luna, medium reasoning | Scheduled Jira backlog assessment and CVE grooming |

Provider selection is per profile. Developer-bot uses OpenAI Responses throughout
a task, including production promotion; there is no cross-provider task handoff.
WatchDuty and backlog-groomer remain scoped to Jira grooming, configured Jenkins
monitoring, and public GitHub repositories. Other repository hosts and their content
in shared checkouts are outside those two profiles' scope. Backlog-groomer applies the fixed
component repository labels without a repository lookup. It adds
`repo:app-interface` only when the **Production image update** column in the
[CVE component mapping](instance/backlog-groomer/agent/.opencode/agents/backlog-grooming.md#cve-component-to-repo-mapping)
is `app-interface`. Maintain that table when a component's release process changes.

Native agent roles and delegation permissions do not create separate OS
sandboxes. WatchDuty's primary agent owns task/memory updates, Slack reporting,
and publication; its MintMaker worker returns scoped local work and evidence.
Developer-bot's coordinator likewise owns lifecycle/publication and delegates
technical work to its domain specialists. Workers cannot recursively delegate.

## Configuration and native resources

Each profile has the following structure:

```text
instance/<profile>/
├── opencode-deployment.json       # Image-owned provider/models and directory grants
└── agent/
    ├── instance.yaml             # Workflow, runtime, provider, model, environments
    ├── AGENTS.md                 # Instance instructions
    ├── opencode.json             # Primary agent and native delegation settings
    ├── project-repos.json        # Repository keys, upstream/fork URLs, host
    ├── mcp.json                  # Optional instance MCP endpoints
    ├── .opencode/
    │   ├── agents/*.md           # Native primary/subagent definitions
    │   ├── instructions/         # Optional coordinator lifecycle instructions
    │   └── skills/<name>/        # SKILL.md, scripts, and reference data
    ├── preflight/                # Optional deterministic instance preflights
    └── workflows/<name>/         # Optional custom workflow AGENTS.md and preflights
```

Remote config sync refreshes the selected `agent/` profile each cycle. Native
resources replace legacy persona prompt directories and Claude instruction
assembly. Deterministic Python preflights still run before model sessions.
Add native agents with V2 YAML frontmatter (`description`, `mode`, `model`, and
ordered `permissions`); update delegation rules when adding a worker. Add local
skills under `.opencode/skills/<name>/` with `name` and `description` frontmatter.

Custom helper scripts run from the synced profile, for example:

```text
/home/botuser/app/data/remote-config/${BOT_CONFIG_PATH}/agent/.opencode/skills/
```

Shared/workflow/environment helpers are still installed by Řehoř under
`.claude/skills/`. Commands such as
`python3 .claude/skills/slack-notify/slack_notify.py ...` deliberately retain that
path; it does not select the Claude runtime. The OpenCode shell restores normal
XDG homes, so custom scripts must not assume the shell's XDG directory is the
native resource staging directory.

Developer-bot's [/wait-for-ci](instance/developer-bot/agent/.opencode/skills/wait-for-ci/SKILL.md)
retains the GitHub CI notification gate and deferred retry behavior. App-interface
promotion MRs send the separate production-update review request immediately;
human review/merge and production verification remain required before closure.

## Image and deployment

Both Konflux pipelines in `.tekton/` build the unmodified upstream
[Dockerfile.runner](dev-bot/Dockerfile.runner) from the repository root.

Developer-bot uses the supported `SLACK_WEBHOOK_URL` for both ordinary updates
and CVE alerts. App-interface maps it to the existing
`ctibor-cve-watchduty-slack-webhook` secret. The shared memory-server sends
incoming app webhooks a `text` payload; developer-bot composes Slack mrkdwn
titles, labelled links, summaries, and next actions directly. Ordinary updates
have no mentions; CVE alerts retain the WatchDuty group mention and their CI
and production gates. Shared helpers' automatic Slack messages are suppressed
where a formatted lifecycle message is composed, avoiding duplicate sends.
The scheduled WatchDuty profile retains its own existing webhook. Its Advisor
and MPEX weekly-status watcher can use `WEEKLY_STATUS_SLACK_WEBHOOK_URL` as an
optional override so those announcements go to the CVE/status channel instead of
the Jenkins WatchDuty channel.

Notifications use immediate mode. Daily digest scheduling is not enabled;
the pinned memory-server's digest sender uses the workflow-specific `msg`
payload and would need incoming-webhook support before enabling digests here.

GlitchTip's helper derives the selected proxy hostname from `EXECUTOR_ADDR`
and connects to port 8448. Its organization defaults to `ccx`; the existing
team proxy supplies the upstream URL and authentication token. This setup needs
no runtime patch, private webhook file, or new Vault credentials.

The push pipeline publishes the instance image; PR images expire after five
days. Production image/template references are managed by
[app-interface's obsint-deploy.yaml](https://gitlab.cee.redhat.com/service/app-interface/-/blob/master/data/services/insights/platform-frontend-ai-dev/obsint-deploy.yaml).
Its existing bot targets supply `BOT_CONFIG_PATH` and the team `PROXY_NAME`.

The [bot template](deploy/template.yaml) sets:

```text
BOT_EXECUTION_ENGINE=coordinator
REHOR_OPENCODE_DEPLOYMENT_CONFIG=/home/botuser/app/${BOT_CONFIG_PATH}/opencode-deployment.json
REHOR_WORKSPACE_OWNER_UID=1000
REHOR_VERTEX_PROXY_URL=http://${PROXY_NAME}:8443
REHOR_MODEL_PROXY_URL=http://${PROXY_NAME}:8450/v1
REHOR_MODEL_PROXY_TOKEN=rehor-runtime
WEEKLY_STATUS_SLACK_WEBHOOK_URL=<optional CVE/status-channel webhook override>
```

The last value is a non-secret client placeholder. Model-provider credentials
remain in the proxy, sourced from Vault-backed Secrets. The
[team proxy template](deploy/proxy-template.yaml) defaults `OPENAI_ALLOWED_MODELS`
to `gpt-6-luna,gpt-6.1-terra,gpt-6.1-sol`; the existing proxy Secret must contain an
OpenAI key with access to GPT-6.1 Sol and GPT-6 Luna. The Vertex allowlist retains only
`claude-sonnet-4-6`. NetworkPolicy permits the corresponding proxy ports, 8443
and 8450. Memory remains shared; GlitchTip keeps its separate team Secret and endpoint.

For the developer-bot switch to Sol, apply the proxy allowlist first, including
any explicit `OPENAI_ALLOWED_MODELS` override in app-interface. The provider
configuration is image-owned: build and deploy a fresh runner image together with
the matching developer-bot profile, keeping the bot paused while they differ.
Remote profile sync alone cannot switch the baked Vertex provider to OpenAI.
Verify a controlled staging cycle uses Sol for the coordinator and specialists
and Luna for title generation before resuming scheduling. Roll back the image
and developer-bot profile together if needed; a rollback to Opus also requires
restoring `claude-opus-4-6` in the Vertex allowlists.

The companion team GitOps update pins the released proxy
`46e07753b6caaab649d306b397c956c3cc3c851e`, which includes OpenAI and Red Hat
registry support plus the Quay/Grype egress fixes. The runner's submodule update
does not update the separately deployed proxy image. See
[the team deployment](https://gitlab.cee.redhat.com/service/app-interface/-/blob/master/data/services/insights/platform-frontend-ai-dev/obsint-deploy.yaml).

## Optional Red Hat registry pulls

The [proxy template](deploy/proxy-template.yaml) exposes a pull-only endpoint on
port 8451. It stays disabled until the optional Vault-backed Secret named by
`REGISTRY_SECRET_NAME` (default `obsint-processing-registry-redhat`) contains
both `registry-redhat-username` and `registry-redhat-token`. One missing
credential is a proxy startup error. Credentials are supplied only to the proxy.

The team already provisions `obsint-processing-registry-redhat` through its
[namespace Vault resource](https://gitlab.cee.redhat.com/service/app-interface/-/blob/master/data/services/insights/platform-frontend-ai-dev/namespaces/stage.hcmais01ue1.yml),
added in [app-interface !217237](https://gitlab.cee.redhat.com/service/app-interface/-/merge_requests/217237).
Reconcile both proxy and bot templates to apply their port 8451 network rules,
and deploy the runner built from this change to install Skopeo.
Syncing profile instructions alone does not install that package.

The CVE skill configures access only when a Red Hat image is needed. It derives
the team proxy hostname from the existing `EXECUTOR_ADDR` and writes a
`~/.config/containers/registries.conf.d/90-rehor-redhat.conf` drop-in mapping
only `registry.redhat.io` to the Service on port 8451. Other registries and client
settings keep their existing behavior. There is no custom routing startup script.
The skill also preserves the Red Hat signature-store configuration for the
proxy hostname; the client's signature policy stays enabled.
Podman, Buildah, and Skopeo keep the original image references. No registry login
is needed in the bot. HTTP is used only to the internal proxy; upstream traffic
uses verified HTTPS. `NO_PROXY` already includes the proxy Service.

Syft scans use Skopeo to download an OCI image through the proxy, then inspect
that local image. Follow the [registry access reference](instance/developer-bot/agent/.opencode/skills/resolve-cve/reference/redhat-registry-proxy.md)
for digest-pinned scans and nested RPM lockfile helpers. Remote Podman needs the
mapping on its service host too.

To disable, remove both registry Secret keys and recycle the proxy. To remove
client routing too, remove the skill's `90-rehor-redhat.conf` drop-in and
`registries.d/team-redhat-proxy.yaml` signature-store entry.
Rehor's authentication client ID
is fixed to `rehor`; there is no `REGISTRY_CLIENT_NAME` template parameter.

See [Rehor #605](https://github.com/OpenShift-Fleet/rehor/pull/605) and its
[registry proxy guide](https://github.com/OpenShift-Fleet/rehor/blob/master/docs/redhat-registry-proxy.md)
for authentication, credential rotation, and CDN access.

## Paired rollout and rollback

Publishing profiles to the tracked config branch can affect an old runner on
its next cycle. A profile update or restart cannot install OpenCode V2 or the
baked provider configuration.

1. Pause all three bots, drain active cycles, and hold KEDA scheduling before
   applying the companion app-interface update. It changes the developer-bot
   webhook mapping as well as the proxy; old profiles still expect the separate
   CVE variable. Record the prior immutable
   image, template settings, compatible config revision, and runtime selection.
2. Merge and deploy the companion app-interface proxy and webhook update first,
   keeping the bots paused until the matching runner and profiles are deployed.
3. Merge this migration and build a fresh image from the pinned submodule.
   Reconcile the released image, template, and matching
   profiles together. Use fresh runner filesystems to avoid stale merged config.
4. Run a controlled staging `--once` cycle for each profile. Check the effective
   provider/model, native primary/delegation, custom/shared helpers, MCP access,
   notification routing, task state, transcript, usage, and child-process cleanup.
   Confirm developer-bot's CI/promotion gates and scheduled dedup behavior.
5. Resume scheduling only after those profile-specific gates pass.

For rollback, pause/drain and hold scheduling again. Restore the previous
Claude-compatible tracked config revision, immutable image, and template/engine
settings together, then start fresh runners and verify them before resuming.
An image-only rollback leaves incompatible remotely synced OpenCode profiles.
Retain Vertex credentials and the previous image throughout the rollback window.

## Local validation

Initialize the pinned submodule and use Node 22 or newer plus the upstream
Python dependencies. Run these checks from the repository root:

```bash
git submodule update --init --recursive
npm --prefix dev-bot/coordinator ci
npm --prefix dev-bot/coordinator run build
npm --prefix dev-bot/coordinator run typecheck
npm --prefix dev-bot/coordinator test -- test/opencode-config.test.ts test/opencode-project-config.test.ts test/opencode-frontmatter.test.ts test/runner.test.ts test/deployment-config.test.ts
python3 -m unittest discover -s instance/watchduty/agent/workflows/watchduty/tests -v
git diff --check
```

Local validation covers coordinator configuration/rendering, native definitions,
helper resolution, and environment compatibility. It does not establish a successful Linux
image build, real provider access, MCP connectivity, or live deployment behavior.
Use the staging gates above and the pinned [Řehoř migration guide](https://github.com/OpenShift-Fleet/rehor/blob/58a87cd9dcf0c27ca41f9c48e847794314a1bfda/docs/migrations/opencode-migration.md)
for those checks.
