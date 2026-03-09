#!/usr/bin/env python3
# Copyright 2024 Flowise Charm Maintainers
# See LICENSE file for licensing details.

"""Unit tests for the Flowise charm."""

import unittest
from unittest.mock import MagicMock, patch

import ops
import ops.testing

# Mock the serialized_data_interface module
mock_sdi = MagicMock()
mock_sdi.NoCompatibleVersions = Exception
mock_sdi.NoVersionsListed = Exception
mock_sdi.get_interfaces = MagicMock(return_value={})

# Mock the data_platform_libs modules
mock_data_interfaces = MagicMock()
mock_data_interfaces.DatabaseCreatedEvent = MagicMock
mock_data_interfaces.DatabaseRequires = MagicMock(return_value=MagicMock())

mock_s3 = MagicMock()
mock_s3.CredentialsChangedEvent = MagicMock
mock_s3.CredentialsGoneEvent = MagicMock
mock_s3.S3Requirer = MagicMock(return_value=MagicMock())

# Mock the redis module
mock_redis = MagicMock()
mock_redis.RedisRelationUpdatedEvent = MagicMock
mock_redis.RedisRequires = MagicMock(return_value=MagicMock())

# Patch the library imports before importing charm
with patch.dict(
    "sys.modules",
    {
        "charms.kubeflow_dashboard.v0.kubeflow_dashboard_links": MagicMock(),
        "charms.data_platform_libs.v0.data_interfaces": mock_data_interfaces,
        "charms.data_platform_libs.v0.s3": mock_s3,
        "charms.redis_k8s.v0.redis": mock_redis,
        "serialized_data_interface": mock_sdi,
    },
):
    from charm import FlowiseCharm


class TestFlowiseCharm(unittest.TestCase):
    """Test cases for the Flowise charm."""

    def setUp(self):
        self.harness = ops.testing.Harness(FlowiseCharm)
        self.addCleanup(self.harness.cleanup)

    def test_pebble_ready(self):
        """Test that Pebble ready event configures the container."""
        self.harness.begin_with_initial_hooks()
        self.harness.set_can_connect("flowise", True)

        # Trigger pebble-ready
        container = self.harness.model.unit.get_container("flowise")
        self.harness.charm.on.flowise_pebble_ready.emit(container)

        # Verify the service is planned
        plan = self.harness.get_container_pebble_plan("flowise")
        self.assertIn("flowise", plan.services)
        self.assertEqual(plan.services["flowise"]["command"], "flowise start")

    def test_default_environment(self):
        """Test default environment variables (no DB, S3, or Redis relations)."""
        self.harness.begin()
        # Mock no database, S3, or Redis config
        self.harness.charm._get_database_config = MagicMock(return_value={})
        self.harness.charm._get_s3_config = MagicMock(return_value={})
        self.harness.charm._get_redis_config = MagicMock(return_value={})

        env = self.harness.charm._flowise_environment()
        self.assertEqual(env["PORT"], "3000")
        # SQLite fallback
        self.assertEqual(env["DATABASE_PATH"], "/root/.flowise")
        self.assertEqual(env["SECRETKEY_PATH"], "/root/.flowise")
        self.assertEqual(env["LOG_PATH"], "/root/.flowise/logs")
        # Local storage fallback
        self.assertEqual(env["STORAGE_TYPE"], "local")
        self.assertEqual(env["BLOB_STORAGE_PATH"], "/root/.flowise/storage")
        self.assertEqual(env["LOG_LEVEL"], "info")
        # Default feature flags
        self.assertEqual(env["CORS_ORIGINS"], "*")
        self.assertEqual(env["SHOW_COMMUNITY_NODES"], "true")

    def test_config_changed_updates_env(self):
        """Test that config changes are reflected in the environment."""
        self.harness.begin()
        self.harness.update_config({"port": 8080, "log-level": "debug"})
        env = self.harness.charm._flowise_environment()
        self.assertEqual(env["PORT"], "8080")
        self.assertEqual(env["LOG_LEVEL"], "debug")

    def test_extra_env_parsing(self):
        """Test that extra-env config is parsed correctly."""
        self.harness.begin()
        self.harness.update_config({
            "extra-env": "OPENAI_API_KEY=sk-test123\nMY_VAR=hello"
        })
        env = self.harness.charm._flowise_environment()
        self.assertEqual(env["OPENAI_API_KEY"], "sk-test123")
        self.assertEqual(env["MY_VAR"], "hello")

    def test_basic_auth_env(self):
        """Test that basic auth config sets the right env vars."""
        self.harness.begin()
        self.harness.update_config({
            "flowise-username": "admin",
            "flowise-password": "secret",
        })
        env = self.harness.charm._flowise_environment()
        self.assertEqual(env["FLOWISE_USERNAME"], "admin")
        self.assertEqual(env["FLOWISE_PASSWORD"], "secret")

    def test_health_check_configured(self):
        """Test that the Pebble health check is set up."""
        self.harness.begin()
        layer = self.harness.charm._pebble_layer()
        self.assertIn("flowise-health", layer["checks"])
        self.assertEqual(
            layer["checks"]["flowise-health"]["http"]["url"],
            "http://localhost:3000/api/v1/ping",
        )

    def test_ingress_relation_sends_data(self):
        """Test that ingress relation sends correct routing data."""
        # Set up mock ingress interface
        mock_ingress = MagicMock()
        mock_sdi.get_interfaces.return_value = {"ingress": mock_ingress}

        self.harness.begin()

        # Add ingress relation
        relation_id = self.harness.add_relation("ingress", "istio-pilot")
        self.harness.add_relation_unit(relation_id, "istio-pilot/0")

        # Trigger the relation changed event
        self.harness.charm._send_ingress_data()

        # Verify send_data was called with correct arguments
        mock_ingress.send_data.assert_called_once()
        call_args = mock_ingress.send_data.call_args[0][0]
        self.assertEqual(call_args["service"], "flowise")
        self.assertEqual(call_args["port"], 3000)
        self.assertEqual(call_args["prefix"], "/flowise/")
        self.assertEqual(call_args["rewrite"], "/")

    def test_ingress_no_relation(self):
        """Test that ingress data sending handles no relation gracefully."""
        mock_sdi.get_interfaces.return_value = {}
        self.harness.begin()
        # Should not raise an exception
        self.harness.charm._send_ingress_data()

    def test_database_config_in_environment(self):
        """Test that database relation config is included in environment."""
        self.harness.begin()
        # Mock database config
        self.harness.charm._get_database_config = MagicMock(return_value={
            "DATABASE_TYPE": "mysql",
            "DATABASE_HOST": "mysql-host",
            "DATABASE_PORT": "3306",
            "DATABASE_NAME": "flowise",
            "DATABASE_USER": "flowise_user",
            "DATABASE_PASSWORD": "secret",
        })
        self.harness.charm._get_s3_config = MagicMock(return_value={})
        self.harness.charm._get_redis_config = MagicMock(return_value={})

        env = self.harness.charm._flowise_environment()
        self.assertEqual(env["DATABASE_TYPE"], "mysql")
        self.assertEqual(env["DATABASE_HOST"], "mysql-host")
        self.assertEqual(env["DATABASE_PORT"], "3306")
        self.assertEqual(env["DATABASE_NAME"], "flowise")
        self.assertEqual(env["DATABASE_USER"], "flowise_user")
        self.assertEqual(env["DATABASE_PASSWORD"], "secret")
        # Should NOT have SQLite path when using external DB
        self.assertNotIn("DATABASE_PATH", env)

    def test_s3_config_in_environment(self):
        """Test that S3 relation config is included in environment."""
        self.harness.begin()
        # Mock S3 config
        self.harness.charm._get_database_config = MagicMock(return_value={})
        self.harness.charm._get_s3_config = MagicMock(return_value={
            "STORAGE_TYPE": "s3",
            "S3_STORAGE_BUCKET_NAME": "flowise-bucket",
            "S3_STORAGE_ACCESS_KEY_ID": "access123",
            "S3_STORAGE_SECRET_ACCESS_KEY": "secret456",
            "S3_STORAGE_REGION": "us-east-1",
            "S3_ENDPOINT_URL": "http://minio:9000",
            "S3_FORCE_PATH_STYLE": "true",
        })
        self.harness.charm._get_redis_config = MagicMock(return_value={})

        env = self.harness.charm._flowise_environment()
        self.assertEqual(env["STORAGE_TYPE"], "s3")
        self.assertEqual(env["S3_STORAGE_BUCKET_NAME"], "flowise-bucket")
        self.assertEqual(env["S3_STORAGE_ACCESS_KEY_ID"], "access123")
        self.assertEqual(env["S3_ENDPOINT_URL"], "http://minio:9000")
        # Should NOT have local storage path when using S3
        self.assertNotIn("BLOB_STORAGE_PATH", env)

    def test_redis_config_enables_queue_mode(self):
        """Test that Redis relation enables queue mode when configured."""
        self.harness.begin()
        # Enable queue mode in config
        self.harness.update_config({"queue-mode": True, "worker-concurrency": 20})

        # Mock Redis config
        self.harness.charm._get_database_config = MagicMock(return_value={})
        self.harness.charm._get_s3_config = MagicMock(return_value={})
        self.harness.charm._get_redis_config = MagicMock(return_value={
            "REDIS_HOST": "redis-host",
            "REDIS_PORT": "6379",
        })

        env = self.harness.charm._flowise_environment()
        self.assertEqual(env["REDIS_HOST"], "redis-host")
        self.assertEqual(env["REDIS_PORT"], "6379")
        self.assertEqual(env["MODE"], "queue")
        self.assertEqual(env["WORKER_CONCURRENCY"], "20")

    def test_queue_mode_without_redis(self):
        """Test that queue mode is not enabled without Redis."""
        self.harness.begin()
        # Enable queue mode but no Redis
        self.harness.update_config({"queue-mode": True})
        self.harness.charm._get_database_config = MagicMock(return_value={})
        self.harness.charm._get_s3_config = MagicMock(return_value={})
        self.harness.charm._get_redis_config = MagicMock(return_value={})

        env = self.harness.charm._flowise_environment()
        # MODE should not be set without Redis
        self.assertNotIn("MODE", env)
        self.assertNotIn("REDIS_HOST", env)

    def test_cors_and_iframe_config(self):
        """Test CORS and iframe origins configuration."""
        self.harness.begin()
        self.harness.update_config({
            "cors-origins": "https://example.com",
            "iframe-origins": "https://app.example.com",
        })
        self.harness.charm._get_database_config = MagicMock(return_value={})
        self.harness.charm._get_s3_config = MagicMock(return_value={})
        self.harness.charm._get_redis_config = MagicMock(return_value={})

        env = self.harness.charm._flowise_environment()
        self.assertEqual(env["CORS_ORIGINS"], "https://example.com")
        self.assertEqual(env["IFRAME_ORIGINS"], "https://app.example.com")

    def test_feature_flags(self):
        """Test feature flag configuration."""
        self.harness.begin()
        self.harness.update_config({
            "show-community-nodes": False,
            "disable-telemetry": True,
            "debug": True,
        })
        self.harness.charm._get_database_config = MagicMock(return_value={})
        self.harness.charm._get_s3_config = MagicMock(return_value={})
        self.harness.charm._get_redis_config = MagicMock(return_value={})

        env = self.harness.charm._flowise_environment()
        self.assertEqual(env["SHOW_COMMUNITY_NODES"], "false")
        self.assertEqual(env["DISABLE_FLOWISE_TELEMETRY"], "true")
        self.assertEqual(env["DEBUG"], "true")

    def test_disabled_nodes_config(self):
        """Test disabled nodes configuration."""
        self.harness.begin()
        self.harness.update_config({
            "disabled-nodes": "chatOpenAI,openAIEmbeddings",
        })
        self.harness.charm._get_database_config = MagicMock(return_value={})
        self.harness.charm._get_s3_config = MagicMock(return_value={})
        self.harness.charm._get_redis_config = MagicMock(return_value={})

        env = self.harness.charm._flowise_environment()
        self.assertEqual(env["DISABLED_NODES"], "chatOpenAI,openAIEmbeddings")


if __name__ == "__main__":
    unittest.main()
