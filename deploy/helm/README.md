# Norse Stack — Helm chart

A minimal but real Helm umbrella chart that packages the three **core** Norse
Stack services as Kubernetes `Deployment` + `Service` objects, and codifies the
localhost trust boundary the [docker-compose](../../docker-compose.yml) stack
gets from `127.0.0.1` binding:

- **`huginn`** — Go strategy engine (singleton; see [SCALING.md](../../docs/SCALING.md))
- **`sleipnir`** — Go execution gateway
- **`muninn`** — Spring Boot market-data / warehouse service

Shared **infrastructure** (Redpanda/Kafka, Postgres, MinIO, Tempo, Prometheus)
is treated as a **documented external dependency**, not re-deployed here. Point
the `externals.*` values at managed infra (MSK/RDS/S3/Grafana Tempo) in prod, or
at in-cluster Helm releases (e.g. Bitnami Kafka/PostgreSQL/MinIO) in dev. This
keeps the chart honest: it owns the stateless app tier and leaves stateful
operators to purpose-built charts.

## What the chart renders

| Object | Count | Purpose |
| --- | --- | --- |
| `Deployment` | 3 | huginn / sleipnir / muninn |
| `Service` | 3 | ClusterIP per service |
| `NetworkPolicy` | 5 | default-deny + allow intra/DNS/scrape/external-egress |
| `ResourceQuota` | 1 | caps namespace blast radius |

The `NetworkPolicy` set re-creates the compose stack's localhost-only posture:
a **default-deny** on ingress *and* egress, then explicit allows for
intra-namespace traffic, DNS, Prometheus scrape (from the `monitoring`
namespace), and egress to the external-infra CIDRs. The `ResourceQuota` is the
analogue of the compose `mem_limit` settings.

> Because the quota requires every pod to declare CPU/memory **requests and
> limits**, all three Deployments set them in `values.yaml`. If you add a
> sidecar, give it requests/limits too or the pod will be rejected.

## Prerequisites

- `helm` v3.8+ (developed against v4)
- A cluster: `kind`, `minikube`, or any conformant Kubernetes
- For the NetworkPolicies to be *enforced*, a CNI that implements them
  (Calico, Cilium). Plain `kind` accepts them but does not enforce — set
  `networkPolicy.enabled=false` if that confuses you, but prefer installing a
  NetworkPolicy-capable CNI.

## Validate without a cluster

```sh
# Lint
helm lint deploy/helm/norse-stack

# Render to stdout and eyeball / pipe to a YAML validator
helm template norse deploy/helm/norse-stack

# Render then schema-check against a live cluster (optional)
helm template norse deploy/helm/norse-stack | kubectl apply --dry-run=client -f -
```

## Install on kind

```sh
kind create cluster --name norse

# (optional) label the monitoring namespace so the scrape NetworkPolicy matches
kubectl create namespace monitoring
kubectl label namespace monitoring kubernetes.io/metadata.name=monitoring --overwrite

# Images: either load locally-built images into kind…
kind load docker-image ghcr.io/lgreene03/huginn:latest --name norse
kind load docker-image ghcr.io/lgreene03/sleipnir:latest --name norse
kind load docker-image ghcr.io/lgreene03/muninn:latest --name norse

# …then install
helm install norse deploy/helm/norse-stack --namespace norse --create-namespace

# Inspect
kubectl -n norse get deploy,svc,networkpolicy,resourcequota
kubectl -n norse port-forward svc/norse-norse-stack-huginn 8081:8081
```

> The core services need Kafka/Postgres/MinIO to be **ready** or they will
> CrashLoop. For a self-contained kind demo, install those first (e.g. Bitnami
> charts) and update `externals.*` to their in-cluster Service names, or run the
> full data plane via docker-compose and only use this chart to validate the
> packaging.

## Install on minikube

```sh
minikube start --cni calico            # Calico => NetworkPolicies are enforced
minikube image load ghcr.io/lgreene03/huginn:latest
minikube image load ghcr.io/lgreene03/sleipnir:latest
minikube image load ghcr.io/lgreene03/muninn:latest
helm install norse deploy/helm/norse-stack --namespace norse --create-namespace
```

## Common overrides

```sh
# Pin image tags
helm upgrade --install norse deploy/helm/norse-stack \
  --set global.imageTag=v0.2.0

# Wire secrets instead of inline defaults (recommended for anything real)
helm upgrade --install norse deploy/helm/norse-stack \
  --set sleipnir.adminTokenSecret.name=sleipnir-admin \
  --set muninn.dbSecret.name=muninn-db \
  --set muninn.minioSecret.name=muninn-minio

# Scale (READ docs/SCALING.md FIRST — huginn is a singleton)
helm upgrade --install norse deploy/helm/norse-stack \
  --set sleipnir.replicaCount=2
```

## Secrets

By default the chart inlines local-dev credentials (matching the compose
defaults). For any non-toy deployment, create Secrets and reference them:

```sh
kubectl -n norse create secret generic muninn-db \
  --from-literal=username=muninn --from-literal=password='<strong>'
kubectl -n norse create secret generic sleipnir-admin \
  --from-literal=admin-token='<strong>'
```

Then set `muninn.dbSecret.name` / `sleipnir.adminTokenSecret.name` as above.

## Uninstall

```sh
helm uninstall norse --namespace norse
kind delete cluster --name norse     # or: minikube delete
```
