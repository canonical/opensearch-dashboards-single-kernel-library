#!/usr/bin/env python3
# Copyright 2026 Canonical Ltd.
# See LICENSE file for licensing details.

"""Collection of global literals for the charm."""

from enum import Enum


class Substrates(Enum):
    """Possible substrates."""

    K8S = "k8s"
    VM = "vm"


class OpenSearchDashboardsPaths:
    """Base Paths for OpenSearch Dashboards Snap."""

    CONF = "etc/opensearch-dashboards"
    DATA = "var/lib/opensearch-dashboards"
    LOGS = "var/log/opensearch-dashboards"
    BIN = "opt/opensearch-dashboards"


# Snap
OPENSEARCH_DASHBOARDS_SNAP_REVISION = "46"
CHARM_KEY = "opensearch-dashboards"

# K8s
CONTAINER_NAME = "opensearch-dashboards"
OSD_SERVICE = "opensearch-dashboards"
EXPORTER_SERVICE = "prometheus-exporter"
LAYER_NAME = "rockcraft-opensearch-dashboards"

# Relation names
PEERS_REL_NAME = "dashboard_peers"
UPGRADE_REL_NAME = "upgrade"
OPENSEARCH_REL_NAME = "opensearch-client"
CERTS_REL_NAME = "certificates"
OAUTH_REL_NAME = "oauth"
INGRESS_REL_NAME = "ingress"
JWT_REL_NAME = "jwt-configuration"
COS_RELATION_NAME = "cos-agent"
PROMETHEUS_RELATION_NAME = "metrics-endpoint"
LOKI_RELATION_NAME = "logging"
GRAFANA_RELATION_NAME = "grafana-dashboard"
STATUS_PEERS_REL_NAME = "status-peers"
RESTART_REL_NAME = "restart"

# Components names
CLUSTER_MANAGER_NAME = "cluster_manager"
CONFIG_MANAGER_NAME = "config_manager"
COS_MANAGER_NAME = "cos_manager"
DASHBOARDS_NAME = "dashboards"
HEALTH_MANAGER_NAME = "health_manager"
UPGRADE_MANAGER_NAME = "upgrade_manager"
TLS_MANAGER_NAME = "tls_manager"
INGRESS_MANAGER_NAME = "ingress_manager"

# OpenSearch user and role
DASHBOARD_INDEX = ".opensearch-dashboards"
DASHBOARD_USER = "kibanaserver"
DASHBOARD_ROLE = "kibana_server"

# Default ports
COS_PORT = 9684
SERVER_PORT = 5601

# Default dependencies for upgrade lib
DEPENDENCIES = {
    "osd_upstream": {
        "dependencies": {"opensearch": "2.19.4"},
        "name": "opensearch-dashboards",
        "upgrade_supported": ">=2",
        "version": "2.19.4",
    },
}

# Paths
BASE_SNAP_DIR = "/var/snap/opensearch-dashboards-charmed"
SNAP_DATA = "current"
SNAP_COMMON = "common"
SNAP = "/snap/opensearch-dashboards-charmed/current"

# Secrets
PEER_APP_SECRETS = ["monitor-username", "monitor-password", "oauth-client-secret"]
PEER_UNIT_SECRETS = ["ca-cert", "csr", "certificate", "private-key"]

# Timeouts
RESTART_TIMEOUT = 30
SERVICE_AVAILABLE_TIMEOUT = 90
