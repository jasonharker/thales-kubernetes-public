import pytest

import flux_ci as f

SYNC = """\
apiVersion: source.toolkit.fluxcd.io/v1
kind: GitRepository
metadata:
  name: thales-infrastructure
  namespace: flux-system
---
apiVersion: kustomize.toolkit.fluxcd.io/v1
kind: Kustomization
metadata:
  name: core
  namespace: flux-system
spec:
  path: ./stacks/core
---
apiVersion: kustomize.toolkit.fluxcd.io/v1
kind: Kustomization
metadata:
  name: apps
  namespace: flux-system
spec:
  path: ./stacks/apps
"""

ENCRYPTED = """\
apiVersion: v1
kind: Secret
metadata:
  name: enc
stringData:
  key: ENC[AES256_GCM,data:abc,type:str]
sops:
  age: []
"""

PLAIN = """\
apiVersion: v1
kind: Secret
metadata:
  name: plain
  namespace: default
data:
  token: aHVudGVyMg==
"""


def test_kustomizations_lists_flux_kustomizations_only(tmp_path):
    p = tmp_path / "sync.yaml"
    p.write_text(SYNC)
    assert f.kustomizations([str(p)]) == [
        ("core", "./stacks/core", str(p)),
        ("apps", "./stacks/apps", str(p)),
    ]


def test_kustomizations_ignores_kustomize_config_kind(tmp_path):
    p = tmp_path / "kustomization.yaml"
    p.write_text("apiVersion: kustomize.config.k8s.io/v1beta1\nkind: Kustomization\nresources: []\n")
    assert f.kustomizations([str(p)]) == []


def test_raw_encrypted_secret_passes():
    assert f.raw_violations("s.yaml", ENCRYPTED) == []


def test_raw_plaintext_secret_fails():
    assert f.raw_violations("s.yaml", PLAIN) == ["s.yaml: Secret default/plain is not SOPS-encrypted"]


def test_raw_plaintext_secret_in_multidoc_fails():
    text = "apiVersion: v1\nkind: ConfigMap\nmetadata:\n  name: c\n---\n" + PLAIN
    assert len(f.raw_violations("s.yaml", text)) == 1


@pytest.mark.parametrize("body", ["", "data: {}\n", "stringData: {}\n"])
def test_raw_secret_without_values_passes(body):
    text = "apiVersion: v1\nkind: Secret\nmetadata:\n  name: empty\n" + body
    assert f.raw_violations("s.yaml", text) == []


def test_raw_unparseable_file_naming_a_secret_fails():
    text = "kind: Secret\nmetadata:\n  name: {{ .Values.name }}\ndata:\n  x: [\n"
    assert f.raw_violations("t.yaml", text) == ["t.yaml: unparseable YAML that declares a Secret"]


def test_raw_unparseable_file_without_secret_passes():
    assert f.raw_violations("t.yaml", "a: [\n") == []


def test_raw_non_secret_passes():
    assert f.raw_violations("c.yaml", "apiVersion: v1\nkind: ConfigMap\nmetadata:\n  name: c\ndata:\n  a: b\n") == []


def test_rendered_masked_secret_passes():
    text = "apiVersion: v1\nkind: Secret\nmetadata:\n  name: m\n  namespace: ceph-csi\nstringData:\n  userKey: '**SOPS**'\n"
    assert f.rendered_violations(text) == []


def test_rendered_plaintext_secret_fails():
    text = "apiVersion: v1\nkind: Secret\nmetadata:\n  name: p\n  namespace: default\nstringData:\n  password: hunter2\n"
    assert f.rendered_violations(text) == ["rendered Secret default/p has unencrypted keys: password"]


def test_rendered_empty_value_passes():
    text = "apiVersion: v1\nkind: Secret\nmetadata:\n  name: e\ndata:\n  blank: ''\n"
    assert f.rendered_violations(text) == []
