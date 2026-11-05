# Red Hat registry access through the team proxy

Use this only for OCP and ACM customer images under `registry.redhat.io`.
The team-configured proxy provides registry access on port 8451. Registry credentials
stay only in the proxy container; the agent only configures its image client
to use that endpoint.
Other registries, including Quay, Docker Hub, and `registry.access.redhat.com`,
keep their existing routes and workflows.

Do not run `podman login`, request/read Vault credentials, or write real registry
credentials into the bot's auth files. The bot needs no registry username or
token. With remote Podman, the same mapping must exist on the Podman service
host. If the mapping, proxy, or registry access is unavailable, report the
blocker. Do not bypass the proxy or interpret a failed scan as NOT AFFECTED.

## Configure access when needed

Before a Red Hat image pull, derive the team proxy hostname from the existing
`EXECUTOR_ADDR` (`<Service>:9090`). Create this client drop-in in the environment
where Podman, Buildah, or Skopeo runs:

```bash
set -euo pipefail
proxy_host=${EXECUTOR_ADDR:?Executor Service address is required}
proxy_host=${proxy_host%:*}
registry_config_dir="$HOME/.config/containers/registries.conf.d"
mkdir -p "$registry_config_dir"
cat > "$registry_config_dir/90-rehor-redhat.conf" <<EOF
[[registry]]
prefix = "registry.redhat.io"
location = "${proxy_host}:8451"
insecure = true
EOF

# Signature discovery follows the remapped hostname. Keep the existing
# signature policy and all other registries' signature-store settings.
signature_config_dir="$HOME/.config/containers/registries.d"
if [ ! -d "$signature_config_dir" ]; then
  mkdir -p "$signature_config_dir"
  cp /etc/containers/registries.d/*.yaml "$signature_config_dir/"
fi
cat > "$signature_config_dir/team-redhat-proxy.yaml" <<EOF
docker:
  "${proxy_host}:8451":
    sigstore: https://registry.redhat.io/containers/sigstore
EOF
```

This is client configuration, not another proxy. The agent performs this step
from the skill; no startup script is installed. Keep other registry settings
intact. The mapping applies only to `registry.redhat.io`, preserving original
image names in commands and Dockerfile `FROM` lines. `insecure = true` permits
HTTP to the internal Service; the team proxy verifies HTTPS upstream. The
deployment's `NO_PROXY` already includes that Service. The second file keeps
Red Hat signature verification working after remapping: signatures come from
the public HTTPS signature store through the existing outbound proxy, without
registry credentials. A new user signature directory overrides the system
directory, which is why its existing files are copied first. Remove only the
two files created by this skill when routing is no longer needed; preserve the
copied settings. Do not replace the client's signature policy.

## Inspect and scan a published image

Syft's direct registry source does not use the containers/image mapping.
Download through Skopeo, then scan the local OCI layout. Set `image` to the
affected customer-published tag from the applicable catalog reference:

```bash
set -euo pipefail
# Example: replace latest with the affected published tag when known.
image=registry.redhat.io/lightspeed-services-ocp/ocp-rules-rhel9:latest
digest=$(skopeo inspect --no-tags --override-os linux --override-arch amd64 \
  --format '{{.Digest}}' "docker://${image}")
image_repo=${image%:*}
image_dir=$(mktemp -d)
skopeo copy --remove-signatures --override-os linux --override-arch amd64 \
  "docker://${image_repo}@${digest}" "oci:${image_dir}/image:scan"
syft "oci-dir:${image_dir}/image" -o json > "${image_dir}/sbom.json"
printf 'Image: %s\nSource digest: %s\nPlatform: linux/amd64\nSBOM: %s\n' \
  "$image" "$digest" "${image_dir}/sbom.json"
```

Use `--no-tags` for inspections: Skopeo's separate repository-tag listing can
bypass registry remapping. This workflow needs only the selected image.

Record the original tag, resolved source digest, platform, and package evidence.
The copy is pinned to that digest so a moving tag cannot change the scan input.
The source signature policy is still enforced. `--remove-signatures` only omits
signatures from the OCI destination, which cannot store these Docker signatures.
OCI conversion may change the local manifest digest; keep the source digest as
the registry evidence. Inspect `sbom.json` and retain the evidence before
removing the temporary image directory. Use the ACM repository from
`insights-on-prem-acm.md` for ACM tickets. Direct Syft registry scans remain
appropriate for the existing Quay workflow.

## Local image builds

The runner's `container-scan` preset installs Buildah. Podman and Docker may be
absent. Use `buildah bud -t localhost/cve-audit -f <Containerfile> .` when needed,
then export the built image for Syft/Grype instead of assuming a Docker daemon:

```bash
image_dir=$(mktemp -d)
buildah push localhost/cve-audit "oci:${image_dir}/image:scan"
syft "oci-dir:${image_dir}/image" -o json > "${image_dir}/sbom.json"
grype "oci-dir:${image_dir}/image" --fail-on medium --only-fixed
```

Retain the evidence before removing the temporary layout and local build image.
If the deployed pod cannot run Buildah or a nested UBI container, report that
local-build blocker and use the required Konflux build checks. Published-image
verification through Skopeo/Syft still runs without a container runtime; failure
to perform it keeps the production gate open.

## RPM lockfile helpers in a nested UBI container

The ACM helper requires `scripts/.dockerconfig.json` to exist and passes it to
Skopeo as `REGISTRY_AUTH_FILE`. When that file is absent, create a credential-free
file containing `{"auths":{}}` for the helper; do not commit it or overwrite an
existing file. Remove the temporary file after the helper finishes.

Run the helper in the required Linux/amd64 UBI9 environment, forwarding the
registry mapping into that environment. Add this mount to its container command:

```bash
-v "$HOME/.config/containers/registries.conf.d:/etc/containers/registries.conf.d:ro"
-v "$HOME/.config/containers/registries.d:/etc/containers/registries.d:ro"
```

Also forward the existing `HTTP_PROXY`, `HTTPS_PROXY`, `NO_PROXY` and lowercase
equivalents using `-e NAME`. The nested container must resolve and reach the
team proxy Service; use the pod's network (`--network=host`) where supported.
Verify `skopeo inspect --no-tags` for the base image inside that container before running
the helper. A custom user-level `registries.conf` inside it may override the
system configuration; in that case install the mapping in that user's
`~/.config/containers/registries.conf.d` instead. Likewise, a user signature
directory overrides the mounted system `registries.d`; preserve and extend its
settings as above. Lightwell authentication is
separate and remains required by the Python dependency workflow.

References: [Rehor proxy guide](https://github.com/OpenShift-Fleet/rehor/blob/master/docs/redhat-registry-proxy.md),
[containers/image registry configuration](https://github.com/containers/image/blob/main/docs/containers-registries.conf.5.md),
[registry drop-ins](https://github.com/containers/image/blob/main/docs/containers-registries.conf.d.5.md),
[signature-store configuration](https://github.com/containers/image/blob/main/docs/containers-registries.d.5.md),
[Syft scan targets](https://oss.anchore.com/docs/guides/sbom/scan-targets/).
