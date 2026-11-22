> **Public mirror.** A sanitized, history-free snapshot of the private GitOps repo that runs the
> THALES homelab cluster. Addresses, VLAN IDs, internal and public hostnames, the tunnel and Ceph
> cluster IDs have been replaced; SOPS ciphertext is placeholder; Talos machine configs are omitted.
> Everything else, structure, manifests, HelmRelease pins, CI, is as deployed.

# THALES Kubernetes Infrastructure

GitOps repository for the THALES homelab Kubernetes cluster (Talos Linux). Everything
running in the cluster reconciles from this repo via Flux CD — raw YAML manifests, plus
Flux HelmReleases for the three Helm-based monitoring stacks. Nothing is hand-applied;
`kubectl apply` / `helm upgrade` against the cluster are regressions.

## Structure

- `clusters/thales/` — Flux bootstrap (`flux-system/`) and `infrastructure-sync.yaml`,
  which defines every Flux Kustomization (GitRepository `thales-infrastructure`, SSH)
- `stacks/` — what the `core`, `platform`, `apps` and `guardrails` Kustomizations apply: lists of
  directories under `infrastructure/` and `apps/`. A new app goes in `stacks/apps/`
- `infrastructure/`
  - `base/` — jenkins
  - `monitoring/` — grafana + exporters (snmp, 5x exportarr, proxmox) as raw manifests
  - `helm/` — HelmRepositories + HelmReleases: loki, promtail, kube-prometheus-stack (versions pinned)
  - `authentik/` — Authentik SSO (server, worker, redis, postgresql)
  - `registry/` — in-cluster Docker registry
  - `limits-quotas/` — LimitRange + ResourceQuota for all namespaces
- `apps/` — `lemp` (portfolio site), `media` (navidrome), `guacamole`, `n8n`, `internal` (homepage)
- `talos/` — Talos machine config snapshots per node (`nodes/*-current.yaml`) and client
  `talosconfig`, all SOPS-encrypted; not applied by Flux
- `namespaces/` — legacy; not referenced by any Kustomization (namespaces live per-app)

## Conventions

- **Secrets**: SOPS + age, `encrypted_regex ^(data|stringData)$`, per-path rules in
  `.sops.yaml`. Note: `.gitignore` excludes `secrets/` directories wholesale, so new
  secret files must be staged with `git add -f`.
- **Images**: pinned (tag or digest) — no `:latest`.
- **Namespaces**: defined per-app (`namespace.yaml`); carry over live labels
  (e.g. `pod-security.kubernetes.io/enforce`) before Flux adopts a namespace.
- **Helm**: only via Flux HelmRelease (`infrastructure/helm/`), chart versions pinned;
  upgrades are a version bump in git.

## Deployment

Flux applies changes automatically: GitRepository sync 1m, Kustomizations 5m.
Force a run: `flux reconcile kustomization <name> --with-source` (from hippodamus).
