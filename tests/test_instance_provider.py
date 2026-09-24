import os
import unittest
from typing import cast
from unittest.mock import patch


os.environ.setdefault("REDIS_USER", "test")
os.environ.setdefault("REDIS_PASSWD", "test")

from hypogeum.choragium import (  # noqa: E402
    DiscoveredInstance,
    DockerInstanceProvider,
    MockInstanceProvider,
    create_docker_client,
    normalize_instance_config,
)
from hypogeum.magnus import _reconcile_target_status, process_one_instance  # noqa: E402


class FakeImage:
    id = "sha256:test"
    tags = ["example/challenge:latest"]


class FakeContainer:
    def __init__(self, status="running", container_id="container-1"):
        self.id = container_id
        self.status = status
        self.labels = {}
        self.image = FakeImage()
        self.removed = False
        self.attrs = {
            "State": {"Status": status},
            "NetworkSettings": {
                "Ports": {"8080/tcp": [{"HostIp": "0.0.0.0", "HostPort": "49152"}]}
            },
        }

    def reload(self):
        self.attrs["State"]["Status"] = self.status

    def remove(self, force=False):
        self.removed = force

    def pause(self):
        self.status = "paused"

    def unpause(self):
        self.status = "running"

    def restart(self, timeout=10):
        self.status = "running"


class FakeContainers:
    def __init__(self):
        self.by_id = {}
        self.last_run_kwargs = None

    def run(self, image, **kwargs):
        self.last_run_kwargs = {"image": image, **kwargs}
        container = FakeContainer()
        container.labels = kwargs["labels"]
        self.by_id[container.id] = container
        return container

    def get(self, container_id):
        return self.by_id[container_id]

    def list(self, all=False, filters=None):
        return list(self.by_id.values())


class FakeClient:
    def __init__(self):
        self.containers = FakeContainers()

    def close(self):
        return None

    def ping(self):
        return True


class InstanceConfigTests(unittest.TestCase):
    def test_defaults_are_bounded_and_provider_neutral(self):
        config = normalize_instance_config(
            {"image": "example/challenge:latest", "container_port": 8080}
        )

        self.assertEqual(config["provider"], "docker")
        self.assertEqual(config["resources"]["memory_mb"], 512)
        self.assertEqual(config["resources"]["cpus"], 1.0)
        self.assertEqual(config["resources"]["pids"], 256)

    def test_rejects_unapproved_capability(self):
        with self.assertRaisesRegex(ValueError, "Unsupported capabilities"):
            normalize_instance_config(
                {
                    "image": "example/challenge:latest",
                    "security": {"cap_add": ["SYS_ADMIN"]},
                }
            )

    def test_missing_remote_environment_uses_local_engine(self):
        client = FakeClient()
        with patch("hypogeum.choragium.env", side_effect=Exception("missing")), patch(
            "hypogeum.choragium.from_env", return_value=client
        ) as from_env:
            self.assertIs(create_docker_client(), client)
            from_env.assert_called_once_with()

    def test_reconciliation_recovers_completed_start(self):
        snapshot = {"status": "starting", "provider_instance_id": None}
        discovered = cast(
            DiscoveredInstance,
            type("DiscoveredInstance", (), {"runtime_status": "running"})(),
        )
        self.assertEqual(_reconcile_target_status(snapshot, discovered), "started")

    def test_reconciliation_marks_missing_active_container_failed(self):
        snapshot = {"status": "started", "provider_instance_id": "missing"}
        self.assertEqual(_reconcile_target_status(snapshot, None), "failed")

    def test_worker_claim_is_scoped_to_provider(self):
        provider = MockInstanceProvider()
        with patch("hypogeum.magnus.claim_next_instance", return_value=None) as claim:
            self.assertFalse(process_one_instance(provider))
        claim.assert_called_once_with("mock")


class DockerProviderTests(unittest.TestCase):
    def setUp(self):
        self.client = FakeClient()
        env_values = {
            "INSTANCE_PUBLIC_HOST": "challenge.example.test",
            "INSTANCE_DOCKER_NETWORK": "bridge",
        }

        def fake_env(keys, defaults="", delimiter=","):
            if keys in env_values:
                return (env_values[keys],)
            return tuple(part.strip() for part in defaults.split(delimiter))

        self.env_patch = patch("hypogeum.choragium.env", side_effect=fake_env)
        self.env_patch.start()
        self.provider = DockerInstanceProvider(self.client)

    def tearDown(self):
        self.env_patch.stop()

    def test_start_applies_runtime_limits_and_random_port(self):
        instance = {
            "sid": 7,
            "cid": 4,
            "pid": "00000000-0000-0000-0000-000000000001",
            "type": "private",
            "status": "starting",
            "provider_instance_id": None,
            "instance_config": {
                "image": "example/challenge:latest",
                "container_port": 8080,
            },
        }

        result = self.provider.apply(instance)
        kwargs = self.client.containers.last_run_kwargs or {}

        self.assertEqual(result.final_status, "started")
        self.assertEqual(result.host, "challenge.example.test")
        self.assertEqual(result.port, 49152)
        self.assertEqual(kwargs["ports"], {"8080/tcp": None})
        self.assertEqual(kwargs["mem_limit"], "512m")
        self.assertEqual(kwargs["nano_cpus"], 1_000_000_000)
        self.assertEqual(kwargs["pids_limit"], 256)
        self.assertEqual(kwargs["cap_drop"], ["ALL"])
        self.assertFalse(kwargs["privileged"])

    def test_mock_provider_preserves_worker_contract(self):
        result = MockInstanceProvider().apply({"status": "stopping"})
        self.assertEqual(result.final_status, "stopped")
        self.assertIsNone(result.provider_instance_id)


if __name__ == "__main__":
    unittest.main()
