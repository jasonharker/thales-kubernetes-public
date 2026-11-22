# Knative Serving

Vendored upstream release manifests, pinned to **knative-vX.Y.Z** (Serving + net-kourier).
Not Helm — Knative ships plain YAML, same reasoning as `gotk-components.yaml`: commit it
verbatim, pin the version in this file, patch config via kustomize rather than editing the
vendored files.

Networking layer: **Kourier**, not Cilium Gateway API. `net-gateway-api` conformance-tests only
Istio/Contour/Envoy Gateway (not Cilium, still Beta), and Cilium X.Y.Z requires Gateway API
vX.Y.Z while Knative tests vX.Y.Z — those CRDs are cluster-scoped singletons, so the versions
would collide. Kourier avoids all of it.

Kourier sits behind the existing in-cluster Traefik (`10.20.130.100`) rather than taking its own
LB-IPAM address — its `kourier` Service is patched LoadBalancer -> ClusterIP in
`serving/config-patches.yaml`, and Traefik routes `*.knative.thales.internal` to
`kourier-internal:80` in `infrastructure/traefik/routes/knative.yaml`.

## Upgrading

1. Re-run the three `curl` fetches below against the new `knative-vX.Y.Z` tag, overwriting the
   vendored files in place.
2. Diff for new/removed CRDs, Deployments, or ConfigMap keys before committing — `config-patches.yaml`
   assumes the `config-network`/`config-domain`/`kourier` Service shapes from v1.23.0.
3. Bump the version string in this file.

```
curl -sL -o crds/serving-crds.yaml    https://github.com/knative/serving/releases/download/knative-vX.Y.Z/serving-crds.yaml
curl -sL -o serving/serving-core.yaml https://github.com/knative/serving/releases/download/knative-vX.Y.Z/serving-core.yaml
curl -sL -o serving/kourier.yaml      https://github.com/knative-extensions/net-kourier/releases/download/knative-vX.Y.Z/kourier.yaml
```

## Known limitation

`3scale-kourier-gateway`'s HPA targets Resource(CPU); metrics-server is not deployed in this
cluster, so it sits at `<unknown>` and never scales past `minReplicas: 1`. Not fatal — deploy
metrics-server separately if that becomes a real bottleneck.
