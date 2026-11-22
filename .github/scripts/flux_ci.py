#!/usr/bin/env python3
"""Checks for flux-ci.yml, which runs them on every PR and every push to main.

  flux_ci.py targets <sync.yaml>...   one "name path file" line per Flux Kustomization
  flux_ci.py secrets-raw              every tracked YAML file: a Secret with values needs SOPS
  flux_ci.py secrets-rendered <f>...  `flux build` output: every Secret value must be **SOPS**

The raw check is the main gate: it covers files no Kustomization renders. The rendered
check catches what only exists after a build. `flux build` masks SOPS values as
**SOPS** and drops `data:` when a Secret also has `stringData:`, so it cannot be the
only check.
"""
import re
import subprocess
import sys

import yaml

MASK = "**SOPS**"
SECRET_RE = re.compile(r"^kind:\s*Secret\s*$", re.M)


def _docs(text):
    return [d for d in yaml.safe_load_all(text) if isinstance(d, dict)]


def _ref(doc):
    meta = doc.get("metadata") or {}
    ns = meta.get("namespace")
    return f"{ns}/{meta.get('name')}" if ns else str(meta.get("name"))


def _values(doc):
    out = {}
    for field in ("data", "stringData"):
        out.update(doc.get(field) or {})
    return out


def kustomizations(files):
    out = []
    for path in files:
        with open(path) as fh:
            for doc in _docs(fh.read()):
                if doc.get("kind") == "Kustomization" and str(doc.get("apiVersion", "")).startswith(
                    "kustomize.toolkit.fluxcd.io/"
                ):
                    out.append((doc["metadata"]["name"], doc["spec"]["path"], path))
    return out


def raw_violations(path, text):
    try:
        docs = _docs(text)
    except yaml.YAMLError:
        return [f"{path}: unparseable YAML that declares a Secret"] if SECRET_RE.search(text) else []
    return [
        f"{path}: Secret {_ref(d)} is not SOPS-encrypted"
        for d in docs
        if d.get("kind") == "Secret" and _values(d) and "sops" not in d
    ]


def rendered_violations(text):
    out = []
    for d in _docs(text):
        if d.get("kind") != "Secret":
            continue
        bad = sorted(k for k, v in _values(d).items() if v not in (MASK, "", None))
        if bad:
            out.append(f"rendered Secret {_ref(d)} has unencrypted keys: {', '.join(bad)}")
    return out


def main(argv):
    cmd, args = argv[0], argv[1:]
    if cmd == "targets":
        for name, path, src in kustomizations(args):
            print(name, path, src)
        return 0
    if cmd == "secrets-raw":
        files = subprocess.run(
            ["git", "ls-files", "-z", "*.yaml", "*.yml"], capture_output=True, text=True, check=True
        ).stdout.split("\0")
        found = []
        for path in filter(None, files):
            with open(path, errors="replace") as fh:
                found += raw_violations(path, fh.read())
    elif cmd == "secrets-rendered":
        found = []
        for path in args:
            with open(path) as fh:
                found += rendered_violations(fh.read())
    else:
        print(f"unknown command {cmd}", file=sys.stderr)
        return 2
    for line in found:
        print(f"::error::{line}")
    return 1 if found else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
