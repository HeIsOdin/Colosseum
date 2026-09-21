"""Container provider implementations for challenge instances."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from time import monotonic, sleep
from typing import Any, Protocol

import docker
from docker.errors import APIError, NotFound
from docker.tls import TLSConfig

from hypogeum.armamentarium import env


MANAGED_LABEL = "com.colosseum.managed"
LABEL_PREFIX = "com.colosseum"
SUPPORTED_CAPABILITIES = {
    "AUDIT_WRITE",
    "CHOWN",
    "DAC_OVERRIDE",
    "FOWNER",
    "FSETID",
    "KILL",
    "MKNOD",
    "NET_BIND_SERVICE",
    "NET_RAW",
    "SETFCAP",
    "SETGID",
    "SETPCAP",
    "SETUID",
    "SYS_CHROOT",
}


@dataclass(frozen=True)
class ProviderResult:
    """Provider state persisted after one lifecycle operation."""

    final_status: str
    host: str | None
    port: int | None
    provider_instance_id: str | None
    provider_metadata: dict[str, Any]


@dataclass(frozen=True)
class DiscoveredInstance:
    """A provider instance found during startup reconciliation."""

    sid: int
    cid: int
    pid: str
    instance_type: str
    provider_instance_id: str
    runtime_status: str
    host: str | None
    port: int | None
    provider_metadata: dict[str, Any]

    @property
    def key(self) -> tuple[int, int, str]:
        return self.sid, self.cid, self.pid


class InstanceProvider(Protocol):
    """Lifecycle surface used by the database worker."""

    name: str

    def apply(self, instance: dict[str, Any]) -> ProviderResult: ...

    def discover(self) -> list[DiscoveredInstance]: ...

    def remove(self, provider_instance_id: str) -> None: ...

    def close(self) -> None: ...


def _bounded_int(value: Any, default: int, minimum: int, maximum: int, field: str) -> int:
    try:
        parsed = int(value if value is not None else default)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field} must be an integer.") from exc
    if not minimum <= parsed <= maximum:
        raise ValueError(f"{field} must be between {minimum} and {maximum}.")
    return parsed


def _bounded_float(value: Any, default: float, minimum: float, maximum: float, field: str) -> float:
    try:
        parsed = float(value if value is not None else default)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field} must be a number.") from exc
    if not minimum <= parsed <= maximum:
        raise ValueError(f"{field} must be between {minimum} and {maximum}.")
    return parsed


def normalize_instance_config(config: Any, require_image: bool = True) -> dict[str, Any]:
    """Validate the provider-neutral configuration stored with a challenge."""
    if config is None:
        config = {}
    if not isinstance(config, dict):
        raise ValueError("instance_config must be a JSON object.")

    allowed = {
        "provider",
        "image",
        "container_port",
        "protocol",
        "environment",
        "resources",
        "security",
        "healthcheck",
    }
    unknown = set(config) - allowed
    if unknown:
        raise ValueError(f"Unsupported instance_config fields: {', '.join(sorted(unknown))}")

    provider = config.get("provider", "docker")
    if provider != "docker":
        raise ValueError("Only the docker instance provider is currently supported.")

    image = config.get("image")
    if require_image and (not isinstance(image, str) or not image.strip()):
        raise ValueError("instance_config.image is required.")
    if image is not None and (not isinstance(image, str) or not image.strip()):
        raise ValueError("instance_config.image must be a non-empty string.")

    container_port = _bounded_int(
        config.get("container_port"),
        8080,
        1,
        65535,
        "instance_config.container_port",
    )
    protocol = str(config.get("protocol", "tcp")).lower()
    if protocol not in {"tcp", "udp"}:
        raise ValueError("instance_config.protocol must be tcp or udp.")

    environment = config.get("environment", {})
    if not isinstance(environment, dict):
        raise ValueError("instance_config.environment must be a JSON object.")
    normalized_environment: dict[str, str] = {}
    for key, value in environment.items():
        if not isinstance(key, str) or not key:
            raise ValueError("instance_config.environment keys must be non-empty strings.")
        if not isinstance(value, (str, int, float, bool)):
            raise ValueError("instance_config.environment values must be scalar values.")
        normalized_environment[key] = str(value)

    resources = config.get("resources", {})
    if not isinstance(resources, dict):
        raise ValueError("instance_config.resources must be a JSON object.")
    resource_unknown = set(resources) - {"memory_mb", "cpus", "pids"}
    if resource_unknown:
        raise ValueError(
            f"Unsupported instance_config.resources fields: {', '.join(sorted(resource_unknown))}"
        )

    max_memory = int(env("INSTANCE_MAX_MEMORY_MB", "2048")[0])
    max_cpus = float(env("INSTANCE_MAX_CPUS", "2")[0])
    max_pids = int(env("INSTANCE_MAX_PIDS", "512")[0])
    normalized_resources = {
        "memory_mb": _bounded_int(resources.get("memory_mb"), 512, 64, max_memory, "memory_mb"),
        "cpus": _bounded_float(resources.get("cpus"), 1.0, 0.1, max_cpus, "cpus"),
        "pids": _bounded_int(resources.get("pids"), 256, 32, max_pids, "pids"),
    }

    security = config.get("security", {})
    if not isinstance(security, dict):
        raise ValueError("instance_config.security must be a JSON object.")
    security_unknown = set(security) - {"read_only", "cap_add"}
    if security_unknown:
        raise ValueError(
            f"Unsupported instance_config.security fields: {', '.join(sorted(security_unknown))}"
        )
    read_only = security.get("read_only", False)
    if not isinstance(read_only, bool):
        raise ValueError("instance_config.security.read_only must be a boolean.")
    cap_add = security.get("cap_add", [])
    if not isinstance(cap_add, list) or not all(isinstance(cap, str) for cap in cap_add):
        raise ValueError("instance_config.security.cap_add must be a list of strings.")
    normalized_capabilities = sorted({cap.upper() for cap in cap_add})
    unsupported_capabilities = set(normalized_capabilities) - SUPPORTED_CAPABILITIES
    if unsupported_capabilities:
        raise ValueError(
            "Unsupported capabilities: " + ", ".join(sorted(unsupported_capabilities))
        )

    healthcheck = config.get("healthcheck", {})
    if not isinstance(healthcheck, dict):
        raise ValueError("instance_config.healthcheck must be a JSON object.")
    health_unknown = set(healthcheck) - {"timeout_seconds"}
    if health_unknown:
        raise ValueError(
            f"Unsupported instance_config.healthcheck fields: {', '.join(sorted(health_unknown))}"
        )
    health_timeout = _bounded_int(
        healthcheck.get("timeout_seconds"),
        30,
        1,
        int(env("INSTANCE_MAX_HEALTH_TIMEOUT", "120")[0]),
        "healthcheck.timeout_seconds",
    )

    normalized: dict[str, Any] = {
        "provider": provider,
        "container_port": container_port,
        "protocol": protocol,
        "environment": normalized_environment,
        "resources": normalized_resources,
        "security": {
            "read_only": read_only,
            "cap_add": normalized_capabilities,
        },
        "healthcheck": {"timeout_seconds": health_timeout},
    }
    if image is not None:
        normalized["image"] = image.strip()
    return normalized


def create_docker_client() -> docker.DockerClient:
    """Use configured mutual TLS when present, otherwise use the local Engine."""
    try:
        remote_url, cert_path, key_path, ca_path = env(
            "DOCKER_REMOTE_URL,DOCKER_TLS_CERT,DOCKER_TLS_KEY,DOCKER_TLS_CA"
        )
    except Exception:
        client = docker.from_env()
        client.ping()
        return client

    certificate_paths = [Path(path).expanduser() for path in (cert_path, key_path, ca_path)]
    missing_paths = [str(path) for path in certificate_paths if not path.is_file()]
    if missing_paths:
        raise ValueError("Docker TLS file(s) not found: " + ", ".join(missing_paths))

    tls = TLSConfig(
        client_cert=(str(certificate_paths[0]), str(certificate_paths[1])),
        ca_cert=str(certificate_paths[2]),
        verify=True,
    )
    client = docker.DockerClient(base_url=remote_url, tls=tls)
    client.ping()
    return client


class DockerInstanceProvider:
    """Docker Engine implementation of the instance lifecycle contract."""

    name = "docker"

    def __init__(self, client: docker.DockerClient | None = None) -> None:
        self.client = client or create_docker_client()
        self.public_host = env("INSTANCE_PUBLIC_HOST", "localhost")[0]
        self.network = env("INSTANCE_DOCKER_NETWORK", "bridge")[0]

    @staticmethod
    def _labels(instance: dict[str, Any]) -> dict[str, str]:
        return {
            MANAGED_LABEL: "true",
            f"{LABEL_PREFIX}.sid": str(instance["sid"]),
            f"{LABEL_PREFIX}.cid": str(instance["cid"]),
            f"{LABEL_PREFIX}.pid": str(instance["pid"]),
            f"{LABEL_PREFIX}.type": str(instance["type"]),
        }

    @staticmethod
    def _name(instance: dict[str, Any]) -> str:
        pid = str(instance["pid"]).replace("-", "")[:12]
        return f"colosseum-{instance['sid']}-{instance['cid']}-{pid}"

    def _find_existing(self, instance: dict[str, Any]):
        provider_id = instance.get("provider_instance_id")
        if provider_id:
            try:
                return self.client.containers.get(provider_id)
            except NotFound:
                pass

        matches = self.client.containers.list(
            all=True,
            filters={"label": [f"{key}={value}" for key, value in self._labels(instance).items()]},
        )
        return matches[0] if matches else None

    @staticmethod
    def _published_port(container, port_key: str) -> int | None:
        container.reload()
        bindings = container.attrs.get("NetworkSettings", {}).get("Ports", {}).get(port_key)
        if not bindings:
            return None
        return int(bindings[0]["HostPort"])

    def _wait_until_ready(self, container, port_key: str, timeout_seconds: int) -> int:
        deadline = monotonic() + timeout_seconds
        while True:
            container.reload()
            state = container.attrs.get("State", {})
            status = state.get("Status", container.status)
            if status in {"dead", "exited", "removing"}:
                raise RuntimeError(f"Container exited while starting with status {status}.")

            health = state.get("Health", {}).get("Status")
            port = self._published_port(container, port_key)
            if status == "running" and port is not None and health in {None, "healthy"}:
                return port
            if health == "unhealthy":
                raise RuntimeError("Container health check reported unhealthy.")
            if monotonic() >= deadline:
                raise TimeoutError("Container did not become ready before the health timeout.")
            sleep(0.25)

    def _create(self, instance: dict[str, Any]) -> ProviderResult:
        config = normalize_instance_config(instance.get("instance_config"))
        resources = config["resources"]
        security = config["security"]
        port_key = f"{config['container_port']}/{config['protocol']}"

        existing = self._find_existing(instance)
        if existing is not None:
            try:
                existing.remove(force=True)
            except NotFound:
                pass

        container = self.client.containers.run(
            config["image"],
            detach=True,
            name=self._name(instance),
            environment=config["environment"],
            labels=self._labels(instance),
            ports={port_key: None},
            network=self.network,
            privileged=False,
            read_only=security["read_only"],
            cap_drop=["ALL"],
            cap_add=security["cap_add"],
            security_opt=["no-new-privileges:true"],
            mem_limit=f"{resources['memory_mb']}m",
            memswap_limit=f"{resources['memory_mb']}m",
            nano_cpus=int(resources["cpus"] * 1_000_000_000),
            pids_limit=resources["pids"],
            init=True,
            restart_policy={"Name": "no"},
            tmpfs={"/tmp": "rw,noexec,nosuid,size=64m"},
        )
        try:
            port = self._wait_until_ready(
                container,
                port_key,
                config["healthcheck"]["timeout_seconds"],
            )
        except Exception:
            try:
                container.remove(force=True)
            except APIError:
                pass
            raise

        return ProviderResult(
            final_status="started",
            host=self.public_host,
            port=port,
            provider_instance_id=container.id,
            provider_metadata={"port_key": port_key, "image": config["image"]},
        )

    def _container_result(self, container, final_status: str, instance: dict[str, Any]) -> ProviderResult:
        metadata = instance.get("provider_metadata") or {}
        config = normalize_instance_config(instance.get("instance_config"))
        port_key = metadata.get("port_key") or f"{config['container_port']}/{config['protocol']}"
        port = self._published_port(container, port_key)
        return ProviderResult(
            final_status=final_status,
            host=self.public_host if port is not None else instance.get("host"),
            port=port if port is not None else instance.get("port"),
            provider_instance_id=container.id,
            provider_metadata={"port_key": port_key, "image": config["image"]},
        )

    def apply(self, instance: dict[str, Any]) -> ProviderResult:
        status = instance["status"]
        if status in {"starting", "resetting"}:
            return self._create(instance)

        container = self._find_existing(instance)
        if status == "stopping":
            if container is not None:
                container.remove(force=True)
            return ProviderResult("stopped", None, None, None, {})
        if container is None:
            raise RuntimeError("The Docker container for this instance was not found.")

        container.reload()
        if status == "pausing":
            if container.status != "paused":
                container.pause()
            return self._container_result(container, "paused", instance)
        if status == "resuming":
            if container.status == "paused":
                container.unpause()
            return self._container_result(container, "started", instance)
        if status == "restarting":
            if container.status == "paused":
                container.unpause()
            container.restart(timeout=10)
            config = normalize_instance_config(instance.get("instance_config"))
            metadata = instance.get("provider_metadata") or {}
            port_key = metadata.get("port_key") or (
                f"{config['container_port']}/{config['protocol']}"
            )
            self._wait_until_ready(
                container,
                port_key,
                config["healthcheck"]["timeout_seconds"],
            )
            result = self._container_result(container, "started", instance)
            return result
        raise ValueError(f"Unsupported intermediate instance state: {status}")

    def discover(self) -> list[DiscoveredInstance]:
        discovered: list[DiscoveredInstance] = []
        containers = self.client.containers.list(
            all=True,
            filters={"label": f"{MANAGED_LABEL}=true"},
        )
        for container in containers:
            container.reload()
            labels = container.labels
            try:
                sid = int(labels[f"{LABEL_PREFIX}.sid"])
                cid = int(labels[f"{LABEL_PREFIX}.cid"])
                pid = labels[f"{LABEL_PREFIX}.pid"]
                instance_type = labels[f"{LABEL_PREFIX}.type"]
            except (KeyError, TypeError, ValueError):
                continue

            ports = container.attrs.get("NetworkSettings", {}).get("Ports", {})
            published_port: int | None = None
            port_key: str | None = None
            for candidate_key, bindings in ports.items():
                if bindings:
                    port_key = candidate_key
                    published_port = int(bindings[0]["HostPort"])
                    break

            discovered.append(
                DiscoveredInstance(
                    sid=sid,
                    cid=cid,
                    pid=pid,
                    instance_type=instance_type,
                    provider_instance_id=container.id,
                    runtime_status=container.status,
                    host=self.public_host if published_port is not None else None,
                    port=published_port,
                    provider_metadata={
                        "port_key": port_key,
                        "image": container.image.tags[0] if container.image.tags else container.image.id,
                    },
                )
            )
        return discovered

    def remove(self, provider_instance_id: str) -> None:
        try:
            self.client.containers.get(provider_instance_id).remove(force=True)
        except NotFound:
            return

    def close(self) -> None:
        self.client.close()


class MockInstanceProvider:
    """Small deterministic provider used by tests without a Docker daemon."""

    name = "mock"

    def apply(self, instance: dict[str, Any]) -> ProviderResult:
        status = instance["status"]
        if status not in {"starting", "pausing", "resuming", "stopping", "restarting", "resetting"}:
            raise ValueError(f"Unsupported intermediate instance state: {status}")
        final_status = {
            "starting": "started",
            "pausing": "paused",
            "resuming": "started",
            "stopping": "stopped",
            "restarting": "started",
            "resetting": "started",
        }[status]
        stopped = final_status == "stopped"
        return ProviderResult(
            final_status=final_status,
            host=None if stopped else "localhost",
            port=None if stopped else 8080,
            provider_instance_id=None if stopped else "mock-instance",
            provider_metadata={} if stopped else {"mock": True},
        )

    def discover(self) -> list[DiscoveredInstance]:
        return []

    def remove(self, provider_instance_id: str) -> None:
        return None

    def close(self) -> None:
        return None

