# Cilium

`values.yaml` here is **not** applied by Flux. The Cilium release is installed by CLI
Helm from hippodamus, deliberately: Flux cannot install the CNI it depends on. The file
is version-controlled so the running configuration is not only in a scratch directory
on one host.

To change Cilium:

    helm upgrade cilium cilium/cilium --version X.Y.Z -n kube-system \
      -f infrastructure/cilium/values.yaml

`lb-ippool.yaml` and `l2-announcement.yaml` **are** reconciled by Flux, via the `cilium`
Kustomization in `clusters/thales/infrastructure-sync.yaml`. They are listed in
`kustomization.yaml`; `values.yaml` and this README are not, so kustomize ignores them.

Two settings that are load-bearing and easy to get wrong:

- `k8sServiceHost: localhost` / `k8sServicePort: 7445` — Talos KubePrism, a per-node API
  load balancer that fails over across all three control planes. Pointing at a
  control-plane address directly makes every agent in the cluster depend on that one
  node, and makes the VIP a prerequisite it does not need to be.

  **Failover proven by test, 2026-08-21 23:04 PDT**, not just by configuration: cp1 was
  rebooted and held `NotReady` for ~70s while observation ran through a kubeconfig
  pointed at cp2. During the outage all five surviving agents stayed ready with
  **zero restarts and zero API errors**, external ingress stayed HTTP 200, and — the
  actual proof — a **new pod was scheduled and networked**, receiving `10.244.1.96` and
  a `CiliumEndpoint` with identity 30660 on w3. Allocating an identity is an API
  *write*, so the agent demonstrably reached the control plane with cp1 gone.
- `securityContext.capabilities` — the chart requests `SYS_MODULE`, which Talos refuses
  outright (`unable to apply caps`). The lists here are the chart's rendered defaults
  minus that one capability, not the Talos docs' published list, which omits `SYSLOG`.

## Monitoring

Agent, operator and Hubble metrics are enabled with ServiceMonitors. This Prometheus
selects ServiceMonitors with an empty selector in an empty namespace selector, so
anything in any namespace is picked up with no matching labels required — verified
against the Prometheus CR rather than assumed.

Verified 2026-08-21: 14 targets up (6 `cilium-agent`, 2 `cilium-operator`,
6 `hubble-metrics`) with real series present.

Hubble metrics are deliberately conservative: plain `dns`, not `dns:query`, which adds
a per-domain label. Incident #28 is the reason — metrics volume has taken Prometheus
down in this cluster before. Add more only with cardinality in mind.

Two things this does **not** give you:

- **No alerting.** `alertmanager.enabled: false` in the kube-prometheus-stack
  HelmRelease is deliberate, so rules evaluate and fire but reach nobody. Cilium alert
  rules would join ~37 existing rules notifying no one. Revisit the Alertmanager
  decision first, or the rules are theatre.
- **No Grafana dashboards.** Grafana here is a standalone deployment with no dashboard
  sidecar, so the chart's `dashboards.enabled` would write ConfigMaps nothing reads.
  Import the Cilium dashboards by hand if wanted.

The chart's kube-proxy monitoring is disabled in
`infrastructure/helm/kube-prometheus-stack.yaml`: kube-proxy was removed when Cilium
took over, so `KubeProxyDown` had become a permanent critical false positive.

⚠️ Still firing and **not** caused by Cilium: `KubeSchedulerDown`,
`KubeControllerManagerDown` and two `TargetDown`. On Talos those components bind to
localhost and cannot be scraped. Pre-existing; fix by disabling them in the chart the
same way, or by exposing them in the Talos config — a real trade-off, not an oversight.

## LB-IPAM in production

`thales-pool` carries Traefik on **10.20.130.100** since 2026-08-22. Anything that DNS
points at must **pin** its address — LB-IPAM otherwise hands out the lowest free one,
which is stable in practice but not guaranteed across Service recreation:

    metadata:
      annotations:
        lbipam.cilium.io/ips: "10.20.130.100"

Verified by requesting `.140` and receiving `.140`. Requesting `.100` would have been
indistinguishable from auto-assignment — design the test so the two answers differ.

**Reachability is a firewall matter, not a Cilium one.** The pool is permitted on port 80
from CLIENTS and INFRA via the pfSense `K8S_LB_POOL` alias. Two traps live there:

- pfSense **silently ignores an IP range** written into an alias outside the GUI. The
  alias saves, the reload succeeds, and the pf table comes back **empty**, so the rule
  matches nothing. Express the pool as CIDRs (`L#84`).
- Verify an alias with `pfctl -t K8S_LB_POOL -T show` and `-T test <ip>`. Never with
  `pfctl -sr | grep <address>` — alias members never appear in rule text, which reports
  a working rule as missing (`L#75`).
