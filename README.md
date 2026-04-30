# Flowise AI Charm for Charmed Kubeflow

A Juju charm that deploys [Flowise](https://flowiseai.com/) — an open-source visual LLM workflow builder — alongside Charmed Kubeflow, with full integration into the Kubeflow Dashboard sidebar.

## Architecture

```
┌─────────────────────────────────────────────────────────┐
│                    Kubeflow Dashboard                    │
│  ┌───────────────────────────────────────────────────┐  │
│  │  Sidebar                                          │  │
│  │  ├─ Notebooks                                     │  │
│  │  ├─ Pipelines                                     │  │
│  │  ├─ Experiments                                   │  │
│  │  ├─ **Flowise AI** ◄── links relation             │  │
│  │  └─ ...                                           │  │
│  └───────────────────────────────────────────────────┘  │
└────────────────────────┬────────────────────────────────┘
                         │ Istio VirtualService
                         ▼
              ┌─────────────────────┐
              │   Flowise (charm)   │
              │   ┌───────────────┐ │
              │   │ OCI container │ │
              │   │ flowiseai/    │ │
              │   │   flowise     │ │
              │   │   :latest     │ │
              │   └───────────────┘ │
              │   Port 3000         │
              │   Storage: PVC      │
              └─────────────────────┘
```

## How It Works

The charm uses two key Juju integrations:

1. **`links` relation** (interface: `kubeflow_dashboard_links`)
   Sends a `DashboardLink` to the Kubeflow Dashboard charm, which adds "Flowise AI" to the sidebar menu. The link points to `/flowise/` and is routed by Istio.

2. **`ingress` relation** (interface: `ingress`)
   Integration with `istio-pilot` for automatic VirtualService creation. This is the recommended approach — no manual kubectl required.

3. **`relational-db` relation** (interface: `mysql_client`)
   Optional integration with MySQL or PostgreSQL for persistent storage. Falls back to SQLite if not configured.

4. **`object-storage` relation** (interface: `s3`)
   Optional integration with S3-compatible storage (MinIO, AWS S3) for file uploads. Falls back to local PVC storage if not configured.

5. **`redis` relation** (interface: `redis`)
   Optional integration with Redis for queue mode and distributed processing. Enables horizontal scaling of workflow execution.

## Prerequisites

- A running Charmed Kubeflow deployment (1.8+)
- `charmcraft` and `juju` CLI tools installed
- Access to the Kubeflow Juju model

## Quick Start

### 1. Build the charm

```bash
cd flowise-charm/

# Fetch required charm libraries
charmcraft fetch-lib charms.kubeflow_dashboard.v0.kubeflow_dashboard_links
charmcraft fetch-lib charms.data_platform_libs.v0.data_interfaces
charmcraft fetch-lib charms.data_platform_libs.v0.s3
charmcraft fetch-lib charms.redis_k8s.v0.redis

# Pack the charm
charmcraft pack
```

### 2. Deploy into the Kubeflow model

```bash
# Switch to your Kubeflow model
juju switch kubeflow

# Deploy the charm with the upstream Flowise OCI image
juju deploy ./flowise_ubuntu-22.04-amd64.charm \
    flowise \
    --resource oci-image=flowiseai/flowise:latest \
    --trust
```

### 3. Integrate with Kubeflow Dashboard

```bash
# Add the sidebar link to the Kubeflow Dashboard
juju integrate flowise:links kubeflow-dashboard:links
```

After a few moments, "Flowise AI" will appear in the Kubeflow Dashboard sidebar.

### 4. Set up Istio routing (recommended)

```bash
juju integrate flowise:ingress istio-pilot:ingress
```

This automatically creates the Istio VirtualService that routes `/flowise/` to the Flowise service. No manual kubectl commands needed — Juju manages the routing lifecycle.

**Alternative — Manual manifests (only if ingress relation unavailable):**

```bash
kubectl apply -f k8s-manifests.yaml -n kubeflow
```

**Optional — Iframe-safe oidc cookies:**

If you embed Flowise (or any other Kubeflow app) in the Central Dashboard's iframe and observe sessions getting damaged on first load, apply this platform-level EnvoyFilter to make oidc-authservice's cookies iframe-friendly (`SameSite=None; Secure; Partitioned; HttpOnly`):

```bash
kubectl apply -f oidc-cookie-iframe-rewrite.yaml
```

The file's header comment explains the failure mode, the trade-offs, and how to verify. Apply only if you've confirmed the symptom in DevTools.

### 5. (Optional) Add database backend

By default, Flowise uses SQLite stored in the PVC. For production, use MySQL or PostgreSQL:

```bash
# Deploy MySQL
juju deploy mysql-k8s --channel 8.0/stable
juju integrate flowise:relational-db mysql-k8s:database
```

### 6. (Optional) Add S3 storage

By default, file uploads are stored in the PVC. For scalable storage, use MinIO or S3:

```bash
# Deploy MinIO and S3 integrator
juju deploy minio
juju deploy s3-integrator
juju integrate s3-integrator minio
juju config s3-integrator bucket=flowise
juju integrate flowise:object-storage s3-integrator:s3-credentials
```

### 7. (Optional) Enable queue mode with Redis

For distributed processing and horizontal scaling:

```bash
# Deploy Redis
juju deploy redis-k8s --channel latest/stable
juju integrate flowise:redis redis-k8s:redis

# Enable queue mode
juju config flowise queue-mode=true worker-concurrency=20
```

### 8. Access Flowise

Open the Kubeflow Dashboard and click **"Flowise AI"** in the sidebar, or navigate directly to:

```
https://<kubeflow-gateway-ip>/flowise/
```

## Configuration

| Config Key                      | Default | Description                                              |
|---------------------------------|---------|----------------------------------------------------------|
| `port`                          | `3000`  | Internal container port                                  |
| `flowise-username`              | (empty) | Basic auth username (optional behind Kubeflow Dex)       |
| `flowise-password`              | (empty) | Basic auth password                                      |
| `flowise-secretkey-overwrite`   | (empty) | Encryption key for stored credentials                    |
| `log-level`                     | `info`  | Log level: error, info, verbose, debug                   |
| `debug`                         | `false` | Enable debug mode                                        |
| `cors-origins`                  | `*`     | Allowed CORS origins (comma-separated)                   |
| `iframe-origins`                | `*`     | Allowed iframe origins (comma-separated)                 |
| `file-size-limit`               | `50mb`  | Maximum file upload size                                 |
| `number-of-proxies`             | `1`     | Number of proxies for rate limiting                      |
| `show-community-nodes`          | `true`  | Show community-created nodes                             |
| `disable-telemetry`             | `false` | Disable Flowise telemetry                                |
| `queue-mode`                    | `false` | Enable queue mode (requires Redis)                       |
| `worker-concurrency`            | `10`    | Queue worker concurrency                                 |
| `disabled-nodes`                | (empty) | Comma-separated list of disabled nodes                   |
| `extra-env`                     | (empty) | Extra env vars as `KEY=VALUE` (one per line)             |

### Passing API keys

Use `extra-env` to inject API keys without baking them into the image:

```bash
juju config flowise extra-env="$(cat <<'EOF'
OPENAI_API_KEY=sk-xxxxxxxxxxxx
ANTHROPIC_API_KEY=sk-ant-xxxxxxxxx
LANGCHAIN_TRACING_V2=true
EOF
)"
```

## Persistent Storage

The charm provisions a 1 GB PVC mounted at `/root/.flowise` to persist:
- Chat flow definitions
- Saved credentials (encrypted)
- Uploaded files and chat logs

To increase the storage size:

```bash
juju deploy ./flowise_ubuntu-22.04-amd64.charm flowise \
    --resource oci-image=flowiseai/flowise:latest \
    --storage flowise-data=10G
```

## Development

### Project structure

```
flowise-charm/
├── charmcraft.yaml          # Charm project definition
├── metadata.yaml            # Charm metadata (relations, resources, storage)
├── requirements.txt         # Python dependencies
├── k8s-manifests.yaml                # Manual Istio routing manifests
├── oidc-cookie-iframe-rewrite.yaml   # Optional: iframe-safe oidc cookies
├── src/
│   └── charm.py             # Main operator logic
└── lib/
    └── charms/
        └── kubeflow_dashboard/
            └── v0/
                └── kubeflow_dashboard_links.py   # Fetched via charmcraft
```

### Fetching charm libraries

```bash
charmcraft fetch-lib charms.kubeflow_dashboard.v0.kubeflow_dashboard_links
charmcraft fetch-lib charms.data_platform_libs.v0.data_interfaces
charmcraft fetch-lib charms.data_platform_libs.v0.s3
charmcraft fetch-lib charms.redis_k8s.v0.redis
```

This downloads the libraries into `lib/` and is required before packing.

### Running tests

```bash
pip install pytest ops-testing
python -m pytest tests/ -v
```

### Using a custom Flowise image

If you build a custom Flowise Docker image (e.g., with extra nodes or plugins):

```bash
# Build and push your custom image
docker build -t myregistry/flowise-custom:1.0 .
docker push myregistry/flowise-custom:1.0

# Deploy or refresh with the custom image
juju deploy ./flowise_ubuntu-22.04-amd64.charm flowise \
    --resource oci-image=myregistry/flowise-custom:1.0
```

## Troubleshooting

### Flowise not appearing in sidebar

```bash
# Check relation status
juju status --relations

# Verify the links relation is active
juju show-unit flowise/0 --format yaml | grep -A5 links
```

### Container not starting

```bash
# Check Pebble logs
juju ssh --container flowise flowise/0 pebble logs flowise

# Check Juju debug logs
juju debug-log --include flowise
```

### Routing issues

```bash
# Verify the VirtualService exists
kubectl get virtualservice flowise -n kubeflow -o yaml

# Test direct pod connectivity
kubectl port-forward -n kubeflow svc/flowise 3000:3000
# Then open http://localhost:3000
```

## License

Apache License 2.0
