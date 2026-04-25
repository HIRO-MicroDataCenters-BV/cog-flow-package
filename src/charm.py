#!/usr/bin/env python3
# Copyright 2024 Flowise Charm Maintainers
# See LICENSE file for licensing details.

"""Charmed Operator for Flowise AI - Visual LLM Workflow Builder."""

import logging

from ops.charm import CharmBase
from ops.main import main
from ops.model import ActiveStatus, BlockedStatus, MaintenanceStatus, WaitingStatus
from ops.pebble import LayerDict
from serialized_data_interface import NoCompatibleVersions, NoVersionsListed, get_interfaces

from charms.data_platform_libs.v0.data_interfaces import DatabaseCreatedEvent, DatabaseRequires
from charms.data_platform_libs.v0.s3 import CredentialsChangedEvent, CredentialsGoneEvent, S3Requirer
from charms.kubeflow_dashboard.v0.kubeflow_dashboard_links import (
    DashboardLink,
    KubeflowDashboardLinksRequirer,
)
from charms.redis_k8s.v0.redis import (
    RedisRelationCharmEvents,
    RedisRelationUpdatedEvent,
    RedisRequires,
)

logger = logging.getLogger(__name__)

FLOWISE_PORT = 3000
FLOWISE_PREFIX = "/flowise/"
FLOWISE_HOME = "/root/.flowise"

# Dashboard link that will appear in the Kubeflow Dashboard sidebar
DASHBOARD_LINKS = [
    DashboardLink(
        text="Flowise AI",
        link="/flowise/",
        type="item",
        icon="account_tree",     # Material icon: flow/tree structure for visual workflows
        location="menu",         # Appears in the main sidebar menu
    ),
]


class FlowiseCharm(CharmBase):
    """Charm the Flowise AI application."""

    # Required for Redis library to emit events
    on = RedisRelationCharmEvents()

    def __init__(self, *args):
        super().__init__(*args)

        # --- Dashboard integration ---
        # This sends the sidebar link to the Kubeflow Dashboard automatically
        # when the "links" relation is established.
        self.kubeflow_dashboard_links = KubeflowDashboardLinksRequirer(
            charm=self,
            relation_name="links",
            dashboard_links=DASHBOARD_LINKS,
        )

        # --- Database integration (MySQL/PostgreSQL) ---
        self.database = DatabaseRequires(
            self,
            relation_name="relational-db",
            database_name="flowise",
        )

        # --- S3 storage integration (MinIO/S3) ---
        self.s3 = S3Requirer(
            self,
            relation_name="object-storage",
            bucket_name="flowise",
        )

        # --- Redis integration (for queue mode) ---
        self.redis = RedisRequires(
            self,
            relation_name="redis",
        )

        # --- Core lifecycle events ---
        self.framework.observe(self.on.flowise_pebble_ready, self._on_pebble_ready)
        self.framework.observe(self.on.config_changed, self._on_config_changed)
        self.framework.observe(self.on.upgrade_charm, self._on_upgrade_charm)

        # --- Ingress relation events (for Istio routing) ---
        self.framework.observe(self.on.ingress_relation_changed, self._on_ingress_relation_changed)

        # --- Database relation events ---
        self.framework.observe(self.database.on.database_created, self._on_database_created)
        self.framework.observe(self.database.on.endpoints_changed, self._on_database_changed)

        # --- S3 relation events ---
        self.framework.observe(self.s3.on.credentials_changed, self._on_s3_credentials_changed)
        self.framework.observe(self.s3.on.credentials_gone, self._on_s3_credentials_gone)

        # --- Redis relation events ---
        self.framework.observe(self.on.redis_relation_updated, self._on_redis_relation_updated)

        # --- Cog API info relation events ---
        self.framework.observe(self.on.cog_api_info_relation_changed, self._on_cog_api_info_changed)

    # -----------------------------------------------------------------
    # Helpers
    # -----------------------------------------------------------------

    def _get_interfaces(self):
        """Get relation interfaces using serialized_data_interface."""
        try:
            return get_interfaces(self)
        except NoVersionsListed as err:
            logger.warning("No versions listed for interface: %s", err)
            return {}
        except NoCompatibleVersions as err:
            logger.warning("No compatible versions for interface: %s", err)
            self.unit.status = BlockedStatus(str(err))
            return {}

    def _send_ingress_data(self):
        """Send ingress configuration to istio-pilot via the ingress relation."""
        interfaces = self._get_interfaces()
        ingress = interfaces.get("ingress")

        if not ingress:
            logger.debug("No ingress relation established yet")
            return

        port = self.config.get("port", FLOWISE_PORT)

        ingress_data = {
            "service": self.app.name,
            "port": port,
            "namespace": self.model.name,
            "prefix": FLOWISE_PREFIX,
            # No rewrite needed - Flowise handles BASE_PATH natively
        }

        ingress.send_data(ingress_data)
        logger.info(
            "Sent ingress data: service=%s, port=%s, prefix=%s",
            self.app.name,
            port,
            FLOWISE_PREFIX,
        )

    def _get_database_config(self) -> dict:
        """Get database configuration from the relational-db relation.

        Returns empty dict if no database relation or not ready.
        Flowise supports: sqlite (default), mysql, postgres
        """
        relations = self.model.relations.get("relational-db", [])
        if not relations:
            return {}

        # Get connection info from the first relation
        relation = relations[0]
        if not relation.app:
            return {}

        # Try to get data from the relation
        try:
            # The DatabaseRequires library provides these methods
            endpoints = self.database.fetch_relation_data().get(relation.id, {})
            if not endpoints:
                return {}

            # Parse endpoints (format: "host:port")
            endpoint = endpoints.get("endpoints", "")
            if not endpoint:
                return {}

            host, _, port = endpoint.partition(":")
            return {
                "DATABASE_TYPE": "mysql",  # or "postgres" based on relation
                "DATABASE_HOST": host,
                "DATABASE_PORT": port or "3306",
                "DATABASE_NAME": endpoints.get("database", "flowise"),
                "DATABASE_USER": endpoints.get("username", ""),
                "DATABASE_PASSWORD": endpoints.get("password", ""),
            }
        except Exception as e:
            logger.warning("Failed to get database config: %s", e)
            return {}

    def _get_s3_config(self) -> dict:
        """Get S3 configuration from the object-storage relation.

        Returns empty dict if no S3 relation or not ready.
        """
        s3_info = self.s3.get_s3_connection_info()
        if not s3_info:
            return {}

        return {
            "STORAGE_TYPE": "s3",
            "S3_STORAGE_BUCKET_NAME": s3_info.get("bucket", "flowise"),
            "S3_STORAGE_ACCESS_KEY_ID": s3_info.get("access-key", ""),
            "S3_STORAGE_SECRET_ACCESS_KEY": s3_info.get("secret-key", ""),
            "S3_STORAGE_REGION": s3_info.get("region", "us-east-1"),
            "S3_ENDPOINT_URL": s3_info.get("endpoint", ""),
            # MinIO typically needs path-style access
            "S3_FORCE_PATH_STYLE": "true",
        }

    def _get_redis_config(self) -> dict:
        """Get Redis configuration from the redis relation.

        Returns empty dict if no Redis relation or not ready.
        Required for queue mode in Flowise.
        """
        redis_data = self.redis.relation_data
        if not redis_data:
            return {}

        hostname = redis_data.get("hostname", "")
        port = redis_data.get("port", "6379")

        if not hostname:
            return {}

        return {
            "REDIS_HOST": hostname,
            "REDIS_PORT": port,
        }

    def _get_cog_api_path(self) -> str:
        """Get the Cog API base path from the cog-api-info relation.

        Returns empty string if relation is not established or data unavailable.
        """
        interfaces = self._get_interfaces()
        cog_api_info = interfaces.get("cog-api-info")
        if not cog_api_info:
            return ""

        try:
            data = cog_api_info.get_data()
            if not data:
                return ""
            api_info = list(data.values())[0]
            base_path = api_info.get("base-path", "")
            if base_path:
                logger.info("Got Cog API base-path from relation: %s", base_path)
            return base_path
        except Exception as err:
            logger.warning("Error reading cog-api-info relation: %s", err)
            return ""

    def _get_cog_api_url(self, base_path: str) -> str:
        """Build the full Cog API URL by combining the K8s service hostname
        of the related app with the given base-path.

        Juju K8s charms expose each application as a same-named ClusterIP
        service in the model's namespace, so http://<remote-app-name> resolves
        from any pod in the model. Returns empty string if the relation is not
        established or its remote app is unknown.

        `base_path` is the value already read from the relation; passed in so
        we don't re-read (and re-log) the relation data twice per event.
        """
        if not base_path:
            return ""
        relations = self.model.relations.get("cog-api-info", [])
        if not relations:
            return ""
        # The relation is declared with no `limit`, but only one cog-api should
        # ever be integrated. If more show up, prefer one with a known remote
        # app and warn — picking relations[0] blindly would be non-deterministic.
        if len(relations) > 1:
            logger.warning(
                "Multiple cog-api-info relations found (%d); using the first "
                "one with a known remote app.",
                len(relations),
            )
        relation = next((r for r in relations if r.app), None)
        if relation is None:
            return ""
        # Ensure exactly one '/' between host and path.
        normalized_path = "/" + base_path.lstrip("/")
        return f"http://{relation.app.name}{normalized_path}"

    def _flowise_environment(self) -> dict:
        """Build the environment dict for the Flowise container.

        See: https://docs.flowiseai.com/configuration/environment-variables
        """
        port = self.config.get("port", FLOWISE_PORT)
        # BASE_PATH for reverse proxy support (no trailing slash)
        # See: https://github.com/FlowiseAI/Flowise/pull/5254
        base_path = self.config.get("base-path", FLOWISE_PREFIX.rstrip("/"))
        env = {
            # Core
            "PORT": str(port),
            "BASE_PATH": base_path,
            "VITE_BASE_PATH": base_path,
            # Auth — trust the header set by Istio/Dex
            "TRUSTED_AUTH_HEADER": self.config.get("trusted-auth-header", "kubeflow-userid"),
            # Secret key storage
            "SECRETKEY_PATH": FLOWISE_HOME,
            # Logging
            "LOG_PATH": f"{FLOWISE_HOME}/logs",
            "LOG_LEVEL": self.config.get("log-level", "info"),
        }

        # --- Debug mode ---
        if self.config.get("debug", False):
            env["DEBUG"] = "true"

        # --- CORS and iframe settings ---
        env["CORS_ORIGINS"] = self.config.get("cors-origins", "*")
        env["IFRAME_ORIGINS"] = self.config.get("iframe-origins", "*")

        # --- File and proxy settings ---
        env["FLOWISE_FILE_SIZE_LIMIT"] = self.config.get("file-size-limit", "50mb")
        env["NUMBER_OF_PROXIES"] = str(self.config.get("number-of-proxies", 1))

        # --- Feature flags ---
        if self.config.get("show-community-nodes", True):
            env["SHOW_COMMUNITY_NODES"] = "true"
        else:
            env["SHOW_COMMUNITY_NODES"] = "false"

        if self.config.get("disable-telemetry", False):
            env["DISABLE_FLOWISE_TELEMETRY"] = "true"

        # --- Disabled nodes ---
        disabled_nodes = self.config.get("disabled-nodes", "")
        if disabled_nodes:
            env["DISABLED_NODES"] = disabled_nodes

        # --- Database configuration ---
        db_config = self._get_database_config()
        if db_config:
            # Use external database (MySQL/PostgreSQL)
            env.update(db_config)
            logger.info("Using external database: %s", db_config.get("DATABASE_HOST"))
        else:
            # Fallback to SQLite (stored in PVC)
            env["DATABASE_PATH"] = FLOWISE_HOME

        # --- S3 storage configuration ---
        s3_config = self._get_s3_config()
        if s3_config:
            # Use S3-compatible storage (MinIO/AWS)
            env.update(s3_config)
            logger.info("Using S3 storage: %s", s3_config.get("S3_ENDPOINT_URL"))
        else:
            # Fallback to local storage (in PVC)
            env["STORAGE_TYPE"] = "local"
            env["BLOB_STORAGE_PATH"] = f"{FLOWISE_HOME}/storage"

        # --- Redis / Queue mode configuration ---
        redis_config = self._get_redis_config()
        queue_mode = self.config.get("queue-mode", False)

        if redis_config:
            env.update(redis_config)
            logger.info("Redis available: %s:%s", redis_config.get("REDIS_HOST"), redis_config.get("REDIS_PORT"))

            if queue_mode:
                env["MODE"] = "queue"
                env["WORKER_CONCURRENCY"] = str(self.config.get("worker-concurrency", 10))
                logger.info("Queue mode enabled with concurrency: %s", env["WORKER_CONCURRENCY"])
        elif queue_mode:
            logger.warning("Queue mode requested but Redis relation not available")

        # --- Optional basic auth (usually not needed behind Kubeflow Dex) ---
        username = self.config.get("flowise-username", "")
        password = self.config.get("flowise-password", "")
        if username and password:
            env["FLOWISE_USERNAME"] = username
            env["FLOWISE_PASSWORD"] = password

        secret_key = self.config.get("flowise-secretkey-overwrite", "")
        if secret_key:
            env["SECRETKEY_OVERWRITE"] = secret_key

        # --- Cog API location (from relation) ---
        # COG_API_PATH is the legacy path-only value; COG_API_URL is the
        # full URL used by the ChatOpenAI Custom node to discover served LLMs.
        cog_api_path = self._get_cog_api_path()
        if cog_api_path:
            env["COG_API_PATH"] = cog_api_path
            cog_api_url = self._get_cog_api_url(cog_api_path)
            if cog_api_url:
                env["COG_API_URL"] = cog_api_url

        # --- Parse extra-env (newline-separated KEY=VALUE pairs) ---
        extra = self.config.get("extra-env", "")
        if extra:
            for line in extra.strip().splitlines():
                line = line.strip()
                if "=" in line and not line.startswith("#"):
                    key, _, value = line.partition("=")
                    env[key.strip()] = value.strip()

        return env

    def _pebble_layer(self) -> LayerDict:
        """Return the Pebble layer configuration for Flowise."""
        port = self.config.get("port", FLOWISE_PORT)
        # Strip trailing "/" so a config like "/flowise/" or "/" doesn't produce a
        # double slash in the URL. "" stays as "" so probing collapses to /api/v1/ping.
        base_path = self.config.get("base-path", FLOWISE_PREFIX.rstrip("/")).rstrip("/")
        ping_url = f"http://localhost:{port}{base_path}/api/v1/ping"
        return {
            "summary": "Flowise AI layer",
            "description": "Pebble layer for the Flowise AI application",
            "services": {
                "flowise": {
                    "override": "replace",
                    "summary": "Flowise AI server",
                    "command": "flowise start",
                    "startup": "enabled",
                    "environment": self._flowise_environment(),
                    "on-check-failure": {
                        "flowise-health": "restart",
                    },
                },
            },
            "checks": {
                "flowise-health": {
                    "override": "replace",
                    "level": "alive",
                    "period": "30s",
                    "timeout": "5s",
                    "http": {
                        "url": ping_url,
                    },
                },
            },
        }

    def _update_layer(self):
        """Push the current Pebble layer and restart if needed."""
        container = self.unit.get_container("flowise")
        if not container.can_connect():
            self.unit.status = WaitingStatus("Waiting for Pebble to be ready")
            return

        self.unit.status = MaintenanceStatus("Configuring Flowise")

        # Create required directories (Flowise expects these to exist)
        for subdir in ["logs", "storage"]:
            path = f"{FLOWISE_HOME}/{subdir}"
            if not container.exists(path):
                container.make_dir(path, make_parents=True)
                logger.info("Created directory: %s", path)

        # Add/update the Pebble layer
        container.add_layer("flowise", self._pebble_layer(), combine=True)
        container.replan()

        port = self.config.get("port", FLOWISE_PORT)
        self.unit.status = ActiveStatus(f"Flowise running on port {port}")

    # -----------------------------------------------------------------
    # Event handlers
    # -----------------------------------------------------------------

    def _on_pebble_ready(self, event):
        """Handle pebble-ready: configure and start Flowise."""
        # Open the port so Juju configures the K8s Service correctly
        port = self.config.get("port", FLOWISE_PORT)
        self.unit.set_ports(port)

        self._update_layer()

    def _on_config_changed(self, event):
        """Handle config-changed: update environment and restart."""
        # Update port in case it changed
        port = self.config.get("port", FLOWISE_PORT)
        self.unit.set_ports(port)

        self._update_layer()

    def _on_upgrade_charm(self, event):
        """Handle upgrade-charm: reapply the Pebble layer."""
        self._update_layer()
        self._send_ingress_data()

    def _on_ingress_relation_changed(self, event):
        """Handle ingress relation changes: send routing config to istio-pilot."""
        self._send_ingress_data()

    def _on_database_created(self, event: DatabaseCreatedEvent):
        """Handle database-created: reconfigure Flowise to use external DB."""
        logger.info("Database created: %s", event.endpoints)
        self._update_layer()

    def _on_database_changed(self, event):
        """Handle database endpoints change: reconfigure Flowise."""
        logger.info("Database endpoints changed")
        self._update_layer()

    def _on_s3_credentials_changed(self, event: CredentialsChangedEvent):
        """Handle S3 credentials change: reconfigure Flowise to use S3 storage."""
        logger.info("S3 credentials changed: bucket=%s", event.bucket)
        self._update_layer()

    def _on_s3_credentials_gone(self, event: CredentialsGoneEvent):
        """Handle S3 credentials removal: fall back to local storage."""
        logger.info("S3 credentials removed, falling back to local storage")
        self._update_layer()

    def _on_redis_relation_updated(self, event: RedisRelationUpdatedEvent):
        """Handle Redis relation update: reconfigure Flowise for queue mode."""
        logger.info("Redis relation updated")
        self._update_layer()

    def _on_cog_api_info_changed(self, event):
        """Handle cog-api-info relation change: update Cog API path."""
        logger.info("Cog API info relation changed")
        self._update_layer()


if __name__ == "__main__":
    main(FlowiseCharm)
