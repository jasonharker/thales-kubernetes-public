#!/usr/bin/env python3
"""Merge gate for Backstage tenant PRs on thales-kubernetes.

tenant-pr.yml auto-merges whatever passes this, so this file is the control. A PR
passes only if it changes exactly one tenant's two paths, its Flux registry entry is
the canonical one, and the rendered tenant holds only the kinds the Backstage
templates emit -- in its own namespace, with no world egress, no cross-namespace
backends, no host access and a preset quota. Design: THALES-Backstage-Design.

Usage: validate_tenant.py <base-ref>   (prints name=<tenant> for $GITHUB_OUTPUT and
writes the rendered tenant to ./render.yaml)
"""
import os
import re
import subprocess
import sys

import yaml

NAME = r"[a-z](?:[a-z0-9-]{0,24}[a-z0-9])?"  # 26 chars max: <name>-backstage-<name> must fit a 63-char DNS label
REGISTRY_RE = re.compile(rf"^tenants/registry/({NAME})\.yaml$")
APP_RE = re.compile(rf"^tenants/apps/({NAME})/[A-Za-z0-9._-]+$")
RESOURCE_RE = re.compile(r"^[A-Za-z0-9._-]+\.yaml$")

ALLOWED_KINDS = {
    ("v1", "Namespace"),
    ("v1", "LimitRange"),
    ("v1", "ResourceQuota"),
    ("v1", "Service"),
    ("networking.k8s.io/v1", "NetworkPolicy"),
    ("cilium.io/v2", "CiliumNetworkPolicy"),
    ("apps/v1", "Deployment"),
    ("traefik.io/v1alpha1", "IngressRoute"),
    ("serving.knative.dev/v1", "Service"),
}

EGRESS_PEERS = [
    {},
    {"matchLabels": {"io.kubernetes.pod.namespace": "kube-system", "k8s-app": "kube-dns"}},
    {"matchLabels": {"io.kubernetes.pod.namespace": "knative-serving"}},
    {"matchLabels": {"io.kubernetes.pod.namespace": "kourier-system"}},
]
INGRESS_NAMESPACES = {"traefik", "knative-serving", "kourier-system"}

_COUNTS = {"services": "10", "configmaps": "5", "secrets": "5"}
QUOTAS = {
    "S": {"requests.cpu": "500m", "requests.memory": "512Mi", "limits.cpu": "2", "limits.memory": "1Gi", "pods": "8", **_COUNTS},
    "M": {"requests.cpu": "1", "requests.memory": "1Gi", "limits.cpu": "4", "limits.memory": "2Gi", "pods": "8", **_COUNTS},
    "L": {"requests.cpu": "2", "requests.memory": "2Gi", "limits.cpu": "8", "limits.memory": "4Gi", "pods": "10", **_COUNTS},
}
MAX_REPLICAS = 3


class Invalid(Exception):
    pass


def tenant_name(changed):
    names = set()
    for path in changed:
        m = REGISTRY_RE.match(path) or APP_RE.match(path)
        if not m:
            raise Invalid(f"path outside a single tenant: {path}")
        names.add(m.group(1))
    if len(names) != 1:
        raise Invalid(f"PR must touch exactly one tenant, got {sorted(names) or 'none'}")
    return names.pop()


def expected_registry(name):
    return {
        "apiVersion": "kustomize.toolkit.fluxcd.io/v1",
        "kind": "Kustomization",
        "metadata": {"name": f"backstage-{name}", "namespace": "flux-system"},
        "spec": {
            "interval": "10m",
            "path": f"./tenants/apps/{name}",
            "prune": True,
            "wait": True,
            "timeout": "3m",
            "targetNamespace": f"backstage-{name}",
            "sourceRef": {"kind": "GitRepository", "name": "thales-infrastructure"},
        },
    }


def check_registry(name, docs):
    if docs != [expected_registry(name)]:
        raise Invalid(f"tenants/registry/{name}.yaml is not the canonical tenant Kustomization")


def check_kustomization(doc):
    if set(doc) != {"apiVersion", "kind", "resources"}:
        raise Invalid(f"kustomization.yaml may set only apiVersion, kind, resources; got {sorted(doc)}")
    if (doc["apiVersion"], doc["kind"]) != ("kustomize.config.k8s.io/v1beta1", "Kustomization"):
        raise Invalid("kustomization.yaml has the wrong apiVersion/kind")
    for r in doc["resources"]:
        if not isinstance(r, str) or not RESOURCE_RE.match(r) or r == "catalog-info.yaml":
            raise Invalid(f"kustomization.yaml resource must be a sibling manifest file: {r!r}")


def _pod(spec, kind, require_no_token):
    for field in ("hostNetwork", "hostPID", "hostIPC"):
        if spec.get(field):
            raise Invalid(f"{kind}: {field} is not allowed")
    for field in ("volumes", "serviceAccountName", "serviceAccount", "initContainers"):
        if field in spec:
            raise Invalid(f"{kind}: {field} is not allowed")
    if require_no_token and spec.get("automountServiceAccountToken") is not False:
        raise Invalid(f"{kind}: automountServiceAccountToken must be false")
    for c in spec.get("containers", []):
        if (c.get("securityContext") or {}).get("privileged"):
            raise Invalid(f"{kind}: privileged containers are not allowed")


def _deployment(name, ns, d):
    if d["spec"].get("replicas", 1) > MAX_REPLICAS:
        raise Invalid(f"Deployment: more than {MAX_REPLICAS} replicas")
    _pod(d["spec"]["template"]["spec"], "Deployment", True)


def _ksvc(name, ns, d):
    _pod(d["spec"]["template"]["spec"], "Knative Service", False)


def _service(name, ns, d):
    if (d.get("spec") or {}).get("type", "ClusterIP") != "ClusterIP":
        raise Invalid("Service: only ClusterIP is allowed")


def _quota(name, ns, d):
    if d["spec"]["hard"] not in QUOTAS.values():
        raise Invalid("ResourceQuota is not one of the S/M/L presets")


def _ingressroute(name, ns, d):
    spec = d["spec"]
    if set(spec) - {"entryPoints", "routes"}:
        raise Invalid("IngressRoute may set only entryPoints and routes")
    if sorted(spec["entryPoints"]) != ["web", "websecure"]:
        raise Invalid("IngressRoute entryPoints must be web and websecure")
    for r in spec["routes"]:
        if r.get("match") != f"Host(`{name}.apps.thales.internal`)":
            raise Invalid(f"IngressRoute match must be Host(`{name}.apps.thales.internal`)")
        if r.get("middlewares") != [{"name": "lan-only", "namespace": "traefik"}]:
            raise Invalid("IngressRoute must use exactly the lan-only middleware")
        for s in r.get("services", []):
            if s.get("namespace", ns) != ns or s.get("kind", "Service") != "Service":
                raise Invalid("IngressRoute backends must be Services in the tenant namespace")


def _cnp(name, ns, d):
    spec = d["spec"]
    if set(spec) - {"endpointSelector", "egress"} or spec.get("endpointSelector") != {}:
        raise Invalid("CiliumNetworkPolicy may only be an all-pods egress policy")
    for rule in spec.get("egress", []):
        if set(rule) - {"toEndpoints", "toPorts"}:
            raise Invalid(f"CiliumNetworkPolicy egress may only use toEndpoints/toPorts, got {sorted(rule)}")
        for peer in rule.get("toEndpoints", []):
            if peer not in EGRESS_PEERS:
                raise Invalid(f"CiliumNetworkPolicy egress peer not allowed: {peer}")


def _netpol(name, ns, d):
    spec = d["spec"]
    if set(spec) - {"podSelector", "policyTypes", "ingress"} or spec.get("podSelector") != {} or spec.get("policyTypes") != ["Ingress"]:
        raise Invalid("NetworkPolicy may only be an all-pods ingress policy")
    for rule in spec.get("ingress", []):
        for peer in rule.get("from", []):
            if peer == {"podSelector": {}}:
                continue
            labels = (peer.get("namespaceSelector") or {}).get("matchLabels") or {}
            if set(peer) != {"namespaceSelector"} or set(labels) != {"kubernetes.io/metadata.name"} or labels["kubernetes.io/metadata.name"] not in INGRESS_NAMESPACES:
                raise Invalid(f"NetworkPolicy ingress peer not allowed: {peer}")


CHECKS = {
    ("apps/v1", "Deployment"): _deployment,
    ("serving.knative.dev/v1", "Service"): _ksvc,
    ("v1", "Service"): _service,
    ("v1", "ResourceQuota"): _quota,
    ("traefik.io/v1alpha1", "IngressRoute"): _ingressroute,
    ("cilium.io/v2", "CiliumNetworkPolicy"): _cnp,
    ("networking.k8s.io/v1", "NetworkPolicy"): _netpol,
}


def check_render(name, docs):
    ns = f"backstage-{name}"
    if [d["metadata"]["name"] for d in docs if d.get("kind") == "Namespace"] != [ns]:
        raise Invalid(f"render must contain exactly one Namespace, {ns}")
    for d in docs:
        key = (d.get("apiVersion"), d.get("kind"))
        if key not in ALLOWED_KINDS:
            raise Invalid(f"kind not allowed in a tenant: {key}")
        if key == ("v1", "Namespace"):
            continue
        if (d.get("metadata") or {}).get("namespace") != ns:
            raise Invalid(f"{d['kind']}/{d['metadata'].get('name')} is not in namespace {ns}")
        check = CHECKS.get(key)
        if check:
            check(name, ns, d)


def main(base):
    changed = subprocess.run(
        ["git", "diff", "--name-only", "--no-renames", f"{base}...HEAD"],
        check=True, capture_output=True, text=True,
    ).stdout.split()
    name = tenant_name(changed)
    registry = f"tenants/registry/{name}.yaml"
    app = f"tenants/apps/{name}"
    if os.path.exists(registry) != os.path.isdir(app):
        raise Invalid("the registry entry and the app directory must be added or removed together")
    if os.path.exists(registry):
        with open(registry) as f:
            check_registry(name, [d for d in yaml.safe_load_all(f) if d is not None])
        with open(f"{app}/kustomization.yaml") as f:
            check_kustomization(yaml.safe_load(f))
        render = subprocess.run(["kubectl", "kustomize", app], check=True, capture_output=True, text=True).stdout
        check_render(name, [d for d in yaml.safe_load_all(render) if d is not None])
        with open("render.yaml", "w") as f:
            f.write(render)
    print(f"name={name}")


if __name__ == "__main__":
    try:
        main(sys.argv[1])
    except Invalid as e:
        print(f"::error::{e}", file=sys.stderr)
        sys.exit(1)
