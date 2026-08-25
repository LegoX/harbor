# Network Policy and No-Hack Evaluations

Harbor tasks can define different network policies for environment startup,
agent execution, and verification.

## Task Configuration

```toml
[environment]
network_mode = "public"

[agent]
network_mode = "allowlist"
allowed_hosts = []

[verifier]
network_mode = "public"
```

Supported modes are:

- `public`: unrestricted networking.
- `no-network`: block external egress.
- `allowlist`: allow only the configured hostnames or IPv4 addresses.

The legacy `allow_internet = false` field remains supported and resolves to a
no-network environment baseline unless an explicit `network_mode` is present.

The phase order is:

1. Apply the environment baseline after startup.
2. Run agent setup under the environment baseline.
3. Apply the agent policy before agent execution.
4. Apply the verifier policy before verification.

Run-specific hosts can be appended with:

```bash
--agent-extra-allowed-host "$HOSTED_LITELLM_HOST"
```

This option affects only an effective `allowlist` or `no-network` agent policy;
it is ignored with a warning when the effective agent policy is public.

## Docker Enforcement

The Docker provider applies `iptables` rules from a short-lived helper
container that joins the main container's network namespace. Only the helper
receives `NET_ADMIN`; the agent container does not retain that capability.

The task image used by the helper must contain `bash` and `iptables`; hostname
allowlists also require `getent`. These packages must be baked into the task
image because the helper starts from that image rather than from a mutated
running container. Policy application fails explicitly when a required tool is
missing.

The implementation:

- permits loopback traffic;
- resolves allowlisted hostnames to IPv4 addresses when the policy is applied;
- permits TCP and UDP to those addresses;
- drops other IPv4 egress;
- filters IPv6, disables it for the namespace, or fails safely if neither is
  possible.

Allowed values are hostnames or IPv4 addresses:

```text
api.openai.com
host.docker.internal
192.168.1.10
```

URLs, ports, CIDRs, paths, and wildcards are rejected. Put the complete URL in
the agent environment, but put only the host in the network allowlist.

## Generate the SWE-Bench Verified No-Hack Dataset

```bash
uv run python scripts/misc/generate_swebench_verified_nohack.py
```

The generator creates a local copy with:

- public environment startup for runtime initialization;
- an agent allowlist that is empty until the model gateway is added at run
  time;
- public verifier networking for dependencies required by the SWE-Bench result
  parser;
- the packages needed for Docker network enforcement.

Create a one-task smoke dataset with:

```bash
uv run python scripts/misc/generate_swebench_verified_nohack.py \
  --limit 1 --overwrite
```

Run a dedicated no-hack wrapper:

```bash
NOHACK_GENERATE_LIMIT=1 N_TASKS=1 N_CONCURRENT=1 \
  bash scripts/run_benchmarks/run_sweb_nohack_c-oh-sdk-1.33.0_glm52fp8.sh
```

Limited runs use a separate generated registry and do not replace the registry
used by a full run.

## Security Boundaries

- Hostnames are resolved once; later DNS changes do not update the rules.
- A host allowlist permits all TCP and UDP ports for that resolved address.
- Allowlisting a host gateway therefore exposes every service reachable at
  that address. Prefer a dedicated model gateway host or isolated network over
  a general-purpose Docker host gateway.
- `iptables` cannot enforce URL paths; use an application gateway for
  path-level policy.
- Dynamic phase policy is implemented for the local Docker provider. Other
  providers may reject or ignore unsupported non-public transitions.
- A public verifier can expose a network path to a process left behind by the
  agent. Stronger isolation requires process cleanup or an offline verifier.
- This workflow prevents ordinary agent egress; it is not a complete sandbox
  against every container or kernel escape technique.
