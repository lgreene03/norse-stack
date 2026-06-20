#!/usr/bin/env python3
"""Validate the monitoring configuration shipped in this repo.

Catches the config-only breakage that `docker compose config` cannot see:
  - prometheus.yml is well-formed YAML with a non-empty scrape_configs list,
    and every scrape job has a job_name + at least one static target.
  - Grafana datasource provisioning files parse, declare apiVersion: 1, and
    every datasource has name/type/url.
  - The Grafana dashboard provider points at the path dashboards are mounted to
    and every referenced dashboard JSON file is valid JSON.

Exits non-zero with a human-readable reason on the first failure so CI fails
loudly instead of shipping a stack that boots with a blank Grafana / no scrape
targets. Stdlib + PyYAML only.

Usage: python3 scripts/validate-monitoring.py
"""

import json
import os
import sys

import yaml

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MON = os.path.join(REPO, "monitoring")

errors = []


def err(msg):
    errors.append(msg)


def load_yaml(path):
    with open(path, "r", encoding="utf-8") as fh:
        return yaml.safe_load(fh)


def validate_prometheus():
    path = os.path.join(MON, "prometheus.yml")
    if not os.path.isfile(path):
        err(f"prometheus.yml missing at {path}")
        return
    try:
        cfg = load_yaml(path)
    except yaml.YAMLError as exc:
        err(f"prometheus.yml is not valid YAML: {exc}")
        return

    scrape = (cfg or {}).get("scrape_configs")
    if not isinstance(scrape, list) or not scrape:
        err("prometheus.yml: scrape_configs must be a non-empty list")
        return

    for i, job in enumerate(scrape):
        if not isinstance(job, dict) or not job.get("job_name"):
            err(f"prometheus.yml: scrape_configs[{i}] missing job_name")
            continue
        name = job["job_name"]
        statics = job.get("static_configs") or []
        targets = [t for s in statics for t in (s.get("targets") or [])]
        if not targets:
            err(f"prometheus.yml: job '{name}' has no static targets")


def validate_datasources():
    ds_dir = os.path.join(MON, "grafana", "provisioning", "datasources")
    if not os.path.isdir(ds_dir):
        err(f"grafana datasources dir missing at {ds_dir}")
        return
    files = [f for f in os.listdir(ds_dir) if f.endswith((".yml", ".yaml"))]
    if not files:
        err("no grafana datasource provisioning files found")
        return
    for f in files:
        path = os.path.join(ds_dir, f)
        try:
            cfg = load_yaml(path)
        except yaml.YAMLError as exc:
            err(f"datasource {f} is not valid YAML: {exc}")
            continue
        if (cfg or {}).get("apiVersion") != 1:
            err(f"datasource {f}: apiVersion must be 1")
        for j, ds in enumerate(((cfg or {}).get("datasources")) or []):
            for key in ("name", "type", "url"):
                if not ds.get(key):
                    err(f"datasource {f}: datasources[{j}] missing '{key}'")


def validate_dashboards():
    prov_dir = os.path.join(MON, "grafana", "provisioning", "dashboards")
    if not os.path.isdir(prov_dir):
        err(f"grafana dashboards provisioning dir missing at {prov_dir}")
        return
    files = [f for f in os.listdir(prov_dir) if f.endswith((".yml", ".yaml"))]
    if not files:
        err("no grafana dashboard provider files found")
        return
    for f in files:
        path = os.path.join(prov_dir, f)
        try:
            cfg = load_yaml(path)
        except yaml.YAMLError as exc:
            err(f"dashboard provider {f} is not valid YAML: {exc}")
            continue
        if (cfg or {}).get("apiVersion") != 1:
            err(f"dashboard provider {f}: apiVersion must be 1")
        for prov in ((cfg or {}).get("providers")) or []:
            if not prov.get("options", {}).get("path"):
                err(f"dashboard provider {f}: provider missing options.path")

    # Every dashboard JSON committed under monitoring/grafana/dashboards must
    # parse as JSON (a malformed dashboard silently fails to load in Grafana).
    dash_dir = os.path.join(MON, "grafana", "dashboards")
    if os.path.isdir(dash_dir):
        for f in os.listdir(dash_dir):
            if not f.endswith(".json"):
                continue
            path = os.path.join(dash_dir, f)
            try:
                with open(path, "r", encoding="utf-8") as fh:
                    json.load(fh)
            except (json.JSONDecodeError, OSError) as exc:
                err(f"dashboard {f} is not valid JSON: {exc}")


def main():
    validate_prometheus()
    validate_datasources()
    validate_dashboards()

    if errors:
        print("Monitoring config validation FAILED:")
        for e in errors:
            print(f"  - {e}")
        sys.exit(1)
    print("Monitoring config OK (prometheus + grafana provisioning + dashboards)")


if __name__ == "__main__":
    main()
