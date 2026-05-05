# OpenSearch Dashboards single-kernel library

Shared Python code for building Juju charms that operate
[OpenSearch Dashboards](https://opensearch.org/docs/latest/dashboards/).

This repository is the single kernel used by OpenSearch Dashboards charms. It
contains the common charm classes, state models, event handlers, managers, and
workload abstractions needed to run the same charm logic on machine and
Kubernetes substrates.

The `single_kernel_opensearch_dashboards/charmcraft.yaml` file is a placeholder
charm definition used to fetch and refresh charm libraries. It is not intended
to be deployed as the product charm by itself.

## Features

- Base charm classes for VM and Kubernetes charms.
- Shared managers for cluster lifecycle, configuration, health checks, TLS,
  upgrades, COS integration, and rolling restarts.
- Event handlers for OpenSearch, TLS certificates, OAuth, JWT configuration,
  OpenSearch Dashboards lifecycle events, and upgrades.
- Workload abstractions for snap-based VM deployments and Kubernetes workloads.
- Structured state and configuration models for charm code.
- Unit, integration, and spread test suites for validating the shared kernel
  against a test charm.

## Repository layout

```text
single_kernel_opensearch_dashboards/
  charms/       Base charm classes for VM and Kubernetes charms.
  common/       Shared constants and exceptions.
  core/         State, config, status, and cluster models.
  events/       Juju event handlers.
  managers/     Operational managers for charm responsibilities.
  workload/     Substrate-specific workload adapters.
  lib/          Vendored charm libraries.
tests/
  unit/         Unit tests for the shared kernel.
  integration/ Integration tests.
  spread/       Spread task definitions.
  charms/vm/    Test VM charm that consumes the library.
terraform/     Terraform module for deploying OpenSearch Dashboards.
scripts/       Development and charm build helpers.
```

## Requirements

- Python 3.10 or later.
- Poetry 2.0 or later.
- Tox, for local test workflows.
- Charmcraft, for packing charms.
- Juju, for integration and spread tests.

## Getting started

Install the project dependencies:

```bash
poetry install --with charm-libs,lint,unit
```

Run the standard local checks:

```bash
tox -e lint
tox -e unit
```

Format the code:

```bash
tox -e format
```

Run integration tests:

```bash
tox -e integration
```

## Using the library in a charm

A consuming charm should import one of the substrate-specific base charm classes
and pass it to `ops.main.main`.

For a machine charm:

```python
#!/usr/bin/env python3

from ops.main import main

from single_kernel_opensearch_dashboards.charms.vm import OpenSearchDashboardsVMCharm


if __name__ == "__main__":
    main(OpenSearchDashboardsVMCharm)
```

For a Kubernetes charm, use:

```python
from single_kernel_opensearch_dashboards.charms.k8s import OpenSearchDashboardsK8sCharm
```

The consuming charm metadata must provide the relation names expected by the
kernel:

- Peer relations: `dashboard_peers`, `restart`, `upgrade`, and `status-peers`.
- Required relation: `opensearch-client`.
- Optional relations: `certificates`, `oauth`, and `jwt-configuration`.
- Provided relation: `cos-agent`.

The test charm under `tests/charms/vm` is a concrete example of the metadata,
configuration, actions, and entrypoint needed by a VM charm.

## Configuration and actions

The shared config model currently exposes:

| Option | Values | Default | Description |
| --- | --- | --- | --- |
| `log_level` | `ERROR`, `WARNING`, `INFO` | `INFO` | Controls OpenSearch Dashboards logging verbosity. |

The test charm exposes these actions:

| Action | Description |
| --- | --- |
| `pre-upgrade-check` | Runs pre-upgrade checks before a charm upgrade. |
| `set-tls-private-key` | Sets or generates the private key used for TLS certificate signing requests. |
| `status-detail` | Returns detailed charm statuses, optionally recomputing them first. |

## Building the test charm

The build helper copies this library into a test charm and packs it:

```bash
./scripts/build.sh
```

To build a specific test charm directory:

```bash
./scripts/build.sh tests/charms/vm
```

When `CI_CACHE=true` is set, the script uses `charmcraftcache`; otherwise it
uses `charmcraft pack`.

## Terraform

The `terraform/` directory contains a Juju Terraform module for deploying the
OpenSearch Dashboards charm and relating it to OpenSearch. See
[`terraform/README.md`](terraform/README.md) for module inputs, outputs, and
usage examples.

## Contributing

Before opening a change, run:

```bash
tox -e lint
tox -e unit
```

For changes that affect charm behavior, also run the relevant integration or
spread tests.

## Community and support

- Homepage: <https://github.com/canonical/opensearch-dashboards-single-kernel-library>
- Issues: <https://github.com/canonical/opensearch-dashboards-single-kernel-library/issues>
- Matrix: <https://matrix.to/#/#charmhub-data-platform:ubuntu.com>

## License

This project is licensed under the Apache License 2.0. See
[`LICENSE`](LICENSE) for details.
