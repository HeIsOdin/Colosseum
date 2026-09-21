# Playground
Playground is where I host all my CTFs

## Challenge instance worker

Run the database-backed Docker worker separately from the web process:

```bash
python -m hypogeum.choragium
```

The worker uses the local Docker Engine when any of the following variables are
missing. When all four are present it connects to the configured remote Engine
with mutual TLS:

```text
DOCKER_REMOTE_URL
DOCKER_TLS_CERT
DOCKER_TLS_KEY
DOCKER_TLS_CA
```

Useful optional settings:

```text
INSTANCE_PUBLIC_HOST=localhost
INSTANCE_DOCKER_NETWORK=bridge
INSTANCE_LEASE=1800
INSTANCE_WORKER_POLL_SECONDS=5
INSTANCE_CLAIM_TIMEOUT=300
INSTANCE_MAX_MEMORY_MB=2048
INSTANCE_MAX_CPUS=2
INSTANCE_MAX_PIDS=512
INSTANCE_MAX_HEALTH_TIMEOUT=120
```

Challenges that require an instance need an `instance_config` JSON object. The
worker applies bounded resource defaults and asks Docker to publish a random
host port:

```json
{
  "provider": "docker",
  "image": "example/challenge:latest",
  "container_port": 8080,
  "protocol": "tcp",
  "environment": {},
  "resources": {
    "memory_mb": 512,
    "cpus": 1,
    "pids": 256
  },
  "security": {
    "read_only": false,
    "cap_add": []
  },
  "healthcheck": {
    "timeout_seconds": 30
  }
}
```

