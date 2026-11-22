import copy

import pytest

import validate_tenant as v

NAME = "demo"
NS = "backstage-demo"


def web_render():
    return [
        {"apiVersion": "v1", "kind": "Namespace", "metadata": {"name": NS}},
        {"apiVersion": "v1", "kind": "LimitRange", "metadata": {"name": "limits", "namespace": NS}, "spec": {"limits": []}},
        {"apiVersion": "v1", "kind": "ResourceQuota", "metadata": {"name": "quota", "namespace": NS}, "spec": {"hard": dict(v.QUOTAS["S"])}},
        {
            "apiVersion": "networking.k8s.io/v1",
            "kind": "NetworkPolicy",
            "metadata": {"name": "allow-ingress", "namespace": NS},
            "spec": {
                "podSelector": {},
                "policyTypes": ["Ingress"],
                "ingress": [
                    {"from": [{"podSelector": {}}]},
                    {
                        "from": [{"namespaceSelector": {"matchLabels": {"kubernetes.io/metadata.name": "traefik"}}}],
                        "ports": [{"port": 8080, "protocol": "TCP"}],
                    },
                ],
            },
        },
        {
            "apiVersion": "cilium.io/v2",
            "kind": "CiliumNetworkPolicy",
            "metadata": {"name": "allow-egress", "namespace": NS},
            "spec": {
                "endpointSelector": {},
                "egress": [
                    {"toEndpoints": [{}]},
                    {
                        "toEndpoints": [{"matchLabels": {"io.kubernetes.pod.namespace": "kube-system", "k8s-app": "kube-dns"}}],
                        "toPorts": [{"ports": [{"port": "53", "protocol": "ANY"}]}],
                    },
                ],
            },
        },
        {
            "apiVersion": "apps/v1",
            "kind": "Deployment",
            "metadata": {"name": NAME, "namespace": NS},
            "spec": {
                "replicas": 1,
                "selector": {"matchLabels": {"app": NAME}},
                "template": {
                    "metadata": {"labels": {"app": NAME}},
                    "spec": {
                        "automountServiceAccountToken": False,
                        "containers": [{"name": "app", "image": "docker.io/nginxinc/nginx-unprivileged:stable-alpine"}],
                    },
                },
            },
        },
        {"apiVersion": "v1", "kind": "Service", "metadata": {"name": NAME, "namespace": NS}, "spec": {"ports": [{"port": 80}]}},
        {
            "apiVersion": "traefik.io/v1alpha1",
            "kind": "IngressRoute",
            "metadata": {"name": NAME, "namespace": NS},
            "spec": {
                "entryPoints": ["web", "websecure"],
                "routes": [
                    {
                        "match": "Host(`demo.apps.thales.internal`)",
                        "kind": "Rule",
                        "middlewares": [{"name": "lan-only", "namespace": "traefik"}],
                        "services": [{"name": NAME, "port": 80}],
                    }
                ],
            },
        },
    ]


def knative_render():
    docs = [d for d in web_render() if d["kind"] in ("Namespace", "LimitRange", "ResourceQuota")]
    docs.append(
        {
            "apiVersion": "networking.k8s.io/v1",
            "kind": "NetworkPolicy",
            "metadata": {"name": "allow-ingress", "namespace": NS},
            "spec": {
                "podSelector": {},
                "policyTypes": ["Ingress"],
                "ingress": [
                    {"from": [{"podSelector": {}}]},
                    {
                        "from": [
                            {"namespaceSelector": {"matchLabels": {"kubernetes.io/metadata.name": "knative-serving"}}},
                            {"namespaceSelector": {"matchLabels": {"kubernetes.io/metadata.name": "kourier-system"}}},
                        ]
                    },
                ],
            },
        }
    )
    docs.append(
        {
            "apiVersion": "cilium.io/v2",
            "kind": "CiliumNetworkPolicy",
            "metadata": {"name": "allow-egress", "namespace": NS},
            "spec": {
                "endpointSelector": {},
                "egress": [
                    {"toEndpoints": [{}]},
                    {
                        "toEndpoints": [{"matchLabels": {"io.kubernetes.pod.namespace": "kube-system", "k8s-app": "kube-dns"}}],
                        "toPorts": [{"ports": [{"port": "53", "protocol": "ANY"}]}],
                    },
                    {
                        "toEndpoints": [
                            {"matchLabels": {"io.kubernetes.pod.namespace": "knative-serving"}},
                            {"matchLabels": {"io.kubernetes.pod.namespace": "kourier-system"}},
                        ]
                    },
                ],
            },
        }
    )
    docs.append(
        {
            "apiVersion": "serving.knative.dev/v1",
            "kind": "Service",
            "metadata": {"name": NAME, "namespace": NS},
            "spec": {"template": {"spec": {"containers": [{"image": "docker.io/nginxinc/nginx-unprivileged:stable-alpine"}]}}},
        }
    )
    return docs


def find(docs, kind):
    return next(d for d in docs if d["kind"] == kind)


# --- paths -----------------------------------------------------------------

def test_paths_one_tenant():
    assert v.tenant_name(["tenants/registry/demo.yaml", "tenants/apps/demo/route.yaml"]) == "demo"


@pytest.mark.parametrize(
    "changed",
    [
        ["tenants/registry/demo.yaml", ".github/workflows/tenant-pr.yml"],
        ["tenants/registry/demo.yaml", "tenants/registry/other.yaml"],
        ["tenants/apps/demo/sub/x.yaml"],
        ["tenants/registry/Demo.yaml"],
        ["tenants/registry/demo-.yaml"],
        ["tenants/registry/" + "a" * 27 + ".yaml"],
        [],
    ],
)
def test_paths_rejected(changed):
    with pytest.raises(v.Invalid):
        v.tenant_name(changed)


def test_name_of_26_chars_allowed():
    name = "a" * 26
    assert v.tenant_name([f"tenants/registry/{name}.yaml"]) == name


# --- registry --------------------------------------------------------------

def test_registry_canonical_passes():
    v.check_registry(NAME, [v.expected_registry(NAME)])


def test_registry_any_drift_fails():
    doc = v.expected_registry(NAME)
    doc["spec"]["path"] = "./infrastructure/vault"
    with pytest.raises(v.Invalid):
        v.check_registry(NAME, [doc])


def test_registry_extra_document_fails():
    with pytest.raises(v.Invalid):
        v.check_registry(NAME, [v.expected_registry(NAME), {"kind": "ClusterRoleBinding"}])


# --- kustomization.yaml ----------------------------------------------------

def test_kustomization_plain_files_pass():
    v.check_kustomization({"apiVersion": "kustomize.config.k8s.io/v1beta1", "kind": "Kustomization", "resources": ["namespace.yaml", "route.yaml"]})


@pytest.mark.parametrize(
    "doc",
    [
        {"apiVersion": "kustomize.config.k8s.io/v1beta1", "kind": "Kustomization", "resources": ["../../../infrastructure/vault"]},
        {"apiVersion": "kustomize.config.k8s.io/v1beta1", "kind": "Kustomization", "resources": ["https://example.com/x.yaml"]},
        {"apiVersion": "kustomize.config.k8s.io/v1beta1", "kind": "Kustomization", "resources": ["catalog-info.yaml"]},
        {"apiVersion": "kustomize.config.k8s.io/v1beta1", "kind": "Kustomization", "resources": ["a.yaml"], "patches": []},
    ],
)
def test_kustomization_rejected(doc):
    with pytest.raises(v.Invalid):
        v.check_kustomization(doc)


# --- render ----------------------------------------------------------------

def test_web_render_passes():
    v.check_render(NAME, web_render())


def test_knative_render_passes():
    v.check_render(NAME, knative_render())


def mutated(mutate, base=web_render):
    docs = copy.deepcopy(base())
    mutate(docs)
    return docs


@pytest.mark.parametrize(
    "mutate",
    [
        pytest.param(lambda d: d.append({"apiVersion": "rbac.authorization.k8s.io/v1", "kind": "ClusterRoleBinding", "metadata": {"name": "x"}}), id="disallowed-kind"),
        pytest.param(lambda d: find(d, "Namespace")["metadata"].update(name="vault"), id="foreign-namespace-object"),
        pytest.param(lambda d: find(d, "Deployment")["metadata"].update(namespace="vault"), id="object-in-foreign-namespace"),
        pytest.param(lambda d: find(d, "CiliumNetworkPolicy")["spec"]["egress"].append({"toEntities": ["world"]}), id="world-egress"),
        pytest.param(lambda d: find(d, "CiliumNetworkPolicy")["spec"]["egress"].append({"toEndpoints": [{"matchLabels": {"io.kubernetes.pod.namespace": "vault"}}]}), id="egress-to-vault"),
        pytest.param(lambda d: find(d, "IngressRoute")["spec"]["routes"][0].update(match="Host(`vault.thales.internal`)"), id="foreign-host"),
        pytest.param(lambda d: find(d, "IngressRoute")["spec"]["routes"][0]["services"][0].update(namespace="vault"), id="cross-namespace-backend"),
        pytest.param(lambda d: find(d, "IngressRoute")["spec"]["routes"][0].update(middlewares=[]), id="no-lan-only"),
        pytest.param(lambda d: find(d, "IngressRoute")["spec"].update(tls={}), id="route-tls"),
        pytest.param(lambda d: find(d, "Deployment")["spec"]["template"]["spec"].update(hostNetwork=True), id="host-network"),
        pytest.param(lambda d: find(d, "Deployment")["spec"]["template"]["spec"].update(volumes=[{"name": "h", "hostPath": {"path": "/"}}]), id="volumes"),
        pytest.param(lambda d: find(d, "Deployment")["spec"]["template"]["spec"].update(serviceAccountName="admin"), id="service-account"),
        pytest.param(lambda d: find(d, "Deployment")["spec"]["template"]["spec"].pop("automountServiceAccountToken"), id="sa-token-mounted"),
        pytest.param(lambda d: find(d, "Deployment")["spec"].update(replicas=4), id="too-many-replicas"),
        pytest.param(lambda d: find(d, "ResourceQuota")["spec"]["hard"].update({"requests.memory": "64Gi"}), id="off-preset-quota"),
        pytest.param(lambda d: find(d, "Service").setdefault("spec", {}).update(type="NodePort"), id="nodeport"),
        pytest.param(lambda d: find(d, "NetworkPolicy")["spec"]["ingress"].append({"from": [{"ipBlock": {"cidr": "0.0.0.0/0"}}]}), id="ipblock-ingress"),
    ],
)
def test_render_rejected(mutate):
    with pytest.raises(v.Invalid):
        v.check_render(NAME, mutated(mutate))


def test_render_missing_namespace_fails():
    with pytest.raises(v.Invalid):
        v.check_render(NAME, [d for d in web_render() if d["kind"] != "Namespace"])
