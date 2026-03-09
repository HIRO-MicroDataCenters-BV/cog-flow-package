# CLAUDE.md — Flowise Juju Charm for Charmed Kubeflow

## What This Project Is

A **Juju Kubernetes charm** that deploys [Flowise AI](https://flowiseai.com/) (open-source visual LLM workflow builder) into a **Charmed Kubeflow** cluster. The charm integrates Flowise into the Kubeflow Dashboard sidebar via the `kubeflow_dashboard_links` relation, so users see "Flowise AI" as a menu item alongside Notebooks, Pipelines, etc.

This is **not** a Flowise fork — it's a Juju operator that wraps the upstream `flowiseai/flowise` OCI image and manages its lifecycle (config, health checks, relations, storage) on Kubernetes.

## Project Structure

```
flowise-charm/
├── CLAUDE.md                # This file — project context for Claude Code
├── README.md                # User-facing docs and deployment guide
├── Makefile                 # Build automation (sets CRAFT_SNAP_CHANNEL automatically)
├── .env                     # Environment variables for build (source before charmcraft)
├── charmcraft.yaml          # Primary charm definition (bases, relations, config, storage)
├── metadata.yaml            # Duplicate of relation/resource declarations (legacy compat)
├── requirements.txt         # Python deps (ops framework, serialized-data-interface)
├── k8s-manifests.yaml       # Manual Istio VirtualService + AuthorizationPolicy
├── src/
│   └── charm.py             # Main operator — single file, all charm logic lives here
├── lib/
│   └── charms/
│       └── kubeflow_dashboard/
│           └── v0/
│               └── kubeflow_dashboard_links.py  # PLACEHOLDER — must be fetched
└── tests/
    └── unit/
        └── test_charm.py    # Unit tests using ops.testing.Harness
```

## Key Architecture Decisions

### Charm Framework
- Uses **`ops` (Python Operator Framework)** — the standard for Canonical/Juju charms.
- Single charm class `FlowiseCharm(CharmBase)` in `src/charm.py`.
- Container management via **Pebble** (the sidecar init system Juju uses in K8s charms).

### Dashboard Integration
- The `links` relation (interface `kubeflow_dashboard_links`) is the critical integration.
- The `KubeflowDashboardLinksRequirer` from the charm library automatically sends a `DashboardLink` to the kubeflow-dashboard charm when the relation is established.
- The link points to `/flowise/` which is routed by Istio VirtualService.
- **location values**: `menu` (main sidebar), `external` (external links section), `quick` (quick links), `documentation`.
- **icon**: Material Design icon name from https://kevingleason.me/Polymer-Todo/bower_components/iron-icons/demo/index.html

### Routing (via ingress relation)
- Flowise runs on port 3000 inside the container.
- The `ingress` relation with `istio-pilot` automatically creates the Istio VirtualService.
- The charm sends ingress data via `serialized_data_interface` library (same pattern as other Kubeflow charms).
- Ingress data includes: `service`, `port`, `namespace`, `prefix` (`/flowise/`), `rewrite` (`/`).
- Fallback: manual `k8s-manifests.yaml` if ingress relation is unavailable.

### Storage
- PVC mounted at `/root/.flowise` (Flowise's default data directory).
- Stores: flow definitions, encrypted credentials, uploaded files, chat logs, SQLite DB.
- Minimum 1G, configurable at deploy time via `--storage flowise-data=10G`.

## How the Charm Works (Event Flow)

1. **`flowise_pebble_ready`** → Pebble sidecar is up → push layer → start Flowise
2. **`config_changed`** → rebuild environment dict → replan Pebble (restarts service)
3. **`upgrade_charm`** → reapply Pebble layer and re-send ingress data
4. **`links_relation_created`** → handled automatically by `KubeflowDashboardLinksRequirer` (no custom code needed)
5. **`ingress_relation_changed`** → send routing data (`service`, `port`, `namespace`, `prefix`, `rewrite`) to istio-pilot

The `_update_layer()` method is the central workhorse — it builds the Pebble layer config, pushes it, and replans.
The `_send_ingress_data()` method sends routing configuration to istio-pilot via the `serialized_data_interface` library.

## Important Technical Details

### Pebble Layer
- Command: `flowise start` (matches official Docker image ENTRYPOINT)
- Health check: HTTP GET `http://localhost:{port}/api/v1/ping` every 30s (returns "pong")
- On health check failure: restart the service
- All config is passed as environment variables (Flowise reads env vars natively)

### Environment Variables (Flowise)
Key Flowise env vars we set (see https://docs.flowiseai.com/configuration/environment-variables):

**Core:**
- `PORT` — server port (default 3000)
- `SECRETKEY_PATH` — encryption key storage (`/root/.flowise`)
- `LOG_PATH`, `LOG_LEVEL`, `DEBUG` — logging config
- `FLOWISE_FILE_SIZE_LIMIT` — max upload size (default 50mb)

**Security & CORS:**
- `CORS_ORIGINS`, `IFRAME_ORIGINS` — allowed origins (default `*`)
- `NUMBER_OF_PROXIES` — proxy count for rate limiting

**Feature Flags:**
- `SHOW_COMMUNITY_NODES` — show community nodes (default true)
- `DISABLE_FLOWISE_TELEMETRY` — disable telemetry
- `DISABLED_NODES` — comma-separated list of disabled nodes

**Database (via relational-db relation or fallback to SQLite):**
- `DATABASE_PATH` — SQLite location (fallback when no DB relation)
- `DATABASE_TYPE` — `mysql` or `postgres` (from relation)
- `DATABASE_HOST`, `DATABASE_PORT`, `DATABASE_NAME`, `DATABASE_USER`, `DATABASE_PASSWORD`

**Storage (via object-storage relation or fallback to local):**
- `STORAGE_TYPE` — `local` or `s3`
- `BLOB_STORAGE_PATH` — local storage path (fallback)
- `S3_STORAGE_BUCKET_NAME`, `S3_STORAGE_ACCESS_KEY_ID`, `S3_STORAGE_SECRET_ACCESS_KEY`
- `S3_ENDPOINT_URL`, `S3_STORAGE_REGION`, `S3_FORCE_PATH_STYLE`

**Redis / Queue Mode (via redis relation):**
- `REDIS_HOST`, `REDIS_PORT` — Redis connection (from relation)
- `MODE` — `queue` when queue-mode is enabled
- `WORKER_CONCURRENCY` — number of queue workers

**Auth:**
- `FLOWISE_USERNAME`, `FLOWISE_PASSWORD` — optional basic auth
- `SECRETKEY_OVERWRITE` — encryption key for stored credentials
- `extra-env` config allows arbitrary env vars (for API keys etc.)

### Charm Library (MUST FETCH)
The file `lib/charms/kubeflow_dashboard/v0/kubeflow_dashboard_links.py` is a **placeholder**. Before packing or running tests, you MUST run:
```bash
charmcraft fetch-lib charms.kubeflow_dashboard.v0.kubeflow_dashboard_links
```
This downloads the real library from Charmhub. Never edit this file directly — it's managed by charmcraft.

### Relation Interfaces
| Relation        | Interface                    | Role     | Purpose                              |
|-----------------|------------------------------|----------|--------------------------------------|
| `links`         | `kubeflow_dashboard_links`   | requirer | Add sidebar entry to KF Dashboard    |
| `ingress`       | `ingress`                    | requirer | Istio routing via istio-pilot        |
| `relational-db` | `mysql_client`               | requirer | MySQL/PostgreSQL for Flowise data    |
| `object-storage`| `s3`                         | requirer | S3/MinIO for file uploads            |
| `redis`         | `redis`                      | requirer | Redis for queue mode and caching     |

### Config Options
| Key                           | Type   | Default | Notes                                    |
|-------------------------------|--------|---------|------------------------------------------|
| `port`                        | int    | 3000    | Must match Flowise internal port         |
| `flowise-username`            | string | ""      | Empty = no basic auth                    |
| `flowise-password`            | string | ""      | Only used if username is also set        |
| `flowise-secretkey-overwrite` | string | ""      | Empty = Flowise generates random key     |
| `log-level`                   | string | "info"  | error/info/verbose/debug                 |
| `extra-env`                   | string | ""      | Newline-separated KEY=VALUE pairs        |

## Development Workflow

### Build Environment
**IMPORTANT**: When using pip-installed charmcraft (not snap), you MUST set the snap channel for the LXD build container:
```bash
export CRAFT_SNAP_CHANNEL=3.x/stable
```
This ensures the LXD container uses charmcraft 3.x snap instead of 4.x (which has bugs). Without this, builds will fail with `charmcraft internal error: KeyError("Failed to get value for 'charmcraft.started_at'")`.

**Options to set this automatically:**
1. Source the `.env` file: `source .env && charmcraft pack`
2. Use the Makefile: `make pack`
3. Use direnv (if installed): `cp .env .envrc && direnv allow`

### Building
```bash
# Fetch charm libraries (required before pack)
charmcraft fetch-lib charms.kubeflow_dashboard.v0.kubeflow_dashboard_links
charmcraft fetch-lib charms.data_platform_libs.v0.data_interfaces
charmcraft fetch-lib charms.data_platform_libs.v0.s3
charmcraft fetch-lib charms.redis_k8s.v0.redis

# Pack the charm (with required env var)
CRAFT_SNAP_CHANNEL=3.x/stable charmcraft pack
```

### Testing
```bash
pip install ops pytest
python -m pytest tests/unit/ -v
```
Tests mock the kubeflow_dashboard library import since it requires charmcraft fetch.

### Deploying (dev cycle)
```bash
juju switch kubeflow
juju deploy ./flowise_ubuntu-22.04-amd64.charm flowise \
    --resource oci-image=flowiseai/flowise:latest --trust
juju integrate flowise:links kubeflow-dashboard:links
juju integrate flowise:ingress istio-pilot:ingress
```

### Refreshing after changes
```bash
charmcraft pack
juju refresh flowise --path=./flowise_ubuntu-22.04-amd64.charm
```

### Debugging
```bash
# Charm status and relations
juju status --relations
juju debug-log --include flowise

# Pebble logs inside container
juju ssh --container flowise flowise/0 pebble logs flowise

# Direct pod access
kubectl port-forward -n kubeflow svc/flowise 3000:3000

# Check if VirtualService is working
kubectl get vs flowise -n kubeflow -o yaml
```

## Common Tasks & Where to Edit

### Add a new config option
1. Add to `config.options` in **both** `charmcraft.yaml` (primary) and keep `metadata.yaml` in sync if it declares config.
2. Read it in `_flowise_environment()` in `src/charm.py`.
3. Add a test in `tests/unit/test_charm.py`.

### Change the dashboard sidebar entry
Edit `DASHBOARD_LINKS` at the top of `src/charm.py`. Fields: `text`, `link`, `type`, `icon`, `location`.

### Add a new relation (e.g., PostgreSQL, Redis, S3)
1. Declare in `requires:` section of `charmcraft.yaml` and `metadata.yaml`.
2. Fetch the relevant charm library: `charmcraft fetch-lib charms.<charm>.v0.<lib>`.
3. Import and instantiate in `FlowiseCharm.__init__()`.
4. If it affects container config, wire it into `_flowise_environment()`.
5. Handle relation events (e.g., `relation_joined`, `relation_changed`).

### Using the relational-db (MySQL/PostgreSQL) integration
The relation is implemented and ready to use:
```bash
# Deploy MySQL
juju deploy mysql-k8s --channel 8.0/stable
# Integrate with Flowise
juju integrate flowise:relational-db mysql-k8s:database
```
Flowise will automatically switch from SQLite to MySQL when the relation is established.

### Using the object-storage (S3/MinIO) integration
The relation is implemented for S3-compatible storage:
```bash
# Deploy MinIO
juju deploy minio
# Deploy S3 integrator
juju deploy s3-integrator
juju integrate s3-integrator minio
# Configure the bucket
juju config s3-integrator bucket=flowise
# Integrate with Flowise
juju integrate flowise:object-storage s3-integrator:s3-credentials
```
Flowise will automatically switch from local storage to S3 when the relation is established.

### Using the Redis integration (for queue mode)
The relation is implemented for Redis-based queue processing:
```bash
# Deploy Redis
juju deploy redis-k8s --channel latest/stable
# Integrate with Flowise
juju integrate flowise:redis redis-k8s:redis
# Enable queue mode
juju config flowise queue-mode=true worker-concurrency=20
```
Queue mode enables distributed processing of Flowise workflows.

### Switch to a custom Flowise OCI image
No charm changes needed — just deploy/refresh with a different `--resource oci-image=<your-image>`.

## Gotchas & Known Issues

- **charmcraft.yaml vs metadata.yaml**: Both exist. `charmcraft.yaml` is the primary source of truth for newer Charmcraft versions. `metadata.yaml` is kept for backward compatibility. Keep them in sync for relations, resources, and containers.
- **Library placeholder**: The charm will NOT pack or run if you haven't fetched the real library. The placeholder file raises `ImportError` intentionally.
- **Flowise base path**: Flowise doesn't natively support a base path prefix (like `/flowise/`). Istio's rewrite rule (`/flowise/ → /`) handles this. If Flowise adds base path support, set `FLOWISE_BASE_URL=/flowise/` in the environment instead.
- **Auth**: Kubeflow already handles auth via Dex/OIDC + Istio. Flowise's built-in basic auth (`FLOWISE_USERNAME`/`FLOWISE_PASSWORD`) is redundant in most Kubeflow deployments but available for extra security.
- **Secret key persistence**: If `flowise-secretkey-overwrite` is not set, Flowise generates a random encryption key on each start. This breaks credential decryption across restarts. Always set this for production.
- **Unit tests mock the library**: Since `kubeflow_dashboard_links` must be fetched via charmcraft, unit tests patch it with `unittest.mock`. Integration tests should use the real library.
- **`BlockedStatus` is imported but unused**: It's there for future relation handling (e.g., DB not ready). Add it when implementing the relational-db relation.

## Upstream References

- **Flowise**: https://github.com/FlowiseAI/Flowise — the app we're deploying
- **Flowise Docs**: https://docs.flowiseai.com — env vars, API, deployment
- **Flowise Docker**: `flowiseai/flowise:latest` on Docker Hub
- **Kubeflow Dashboard Operator**: https://github.com/canonical/kubeflow-dashboard-operator
- **Dashboard Links Library**: https://charmhub.io/kubeflow-dashboard/libraries/kubeflow_dashboard_links
- **Istio Operators**: https://github.com/canonical/istio-operators — istio-pilot charm
- **Serialized Data Interface**: https://github.com/canonical/serialized-data-interface — ingress relation library
- **Ops Framework**: https://ops.readthedocs.io/en/latest/
- **Charmcraft Docs**: https://documentation.ubuntu.com/charmcraft/
- **Charmed Kubeflow**: https://documentation.ubuntu.com/charmed-kubeflow/
- **Pebble**: https://documentation.ubuntu.com/pebble/

## Code Style

- Python 3.8+ compatible (Ubuntu 22.04 base).
- Follow Canonical charm conventions: single `charm.py`, lifecycle events, Pebble layers.
- Docstrings on all public methods.
- Type hints where practical (the ops framework uses them extensively).
- Logging via `logger = logging.getLogger(__name__)` — use `logger.info()`, `logger.warning()`, etc.
- Tests use `ops.testing.Harness` — the standard for charm unit testing.
