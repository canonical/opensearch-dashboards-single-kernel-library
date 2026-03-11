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
OPENSEARCH_DASHBOARDS_SNAP_REVISION = "54"
CHARM_KEY = "opensearch-dashboards"

# Relation names
PEERS_REL_NAME = "dashboard_peers"
UPGRADE_REL_NAME = "upgrade"
OPENSEARCH_REL_NAME = "opensearch-client"
CERTS_REL_NAME = "certificates"
OAUTH_REL_NAME = "oauth"
JWT_REL_NAME = "jwt-configuration"
COS_RELATION_NAME = "cos-agent"

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
BASE_SNAP_DIR = "/var/snap/opensearch-dashboards"
SNAP_DATA = "current"
SNAP_COMMON = "common"
SNAP = "/snap/opensearch-dashboards/current"

# Secrets
PEER_APP_SECRETS = ["monitor-username", "monitor-password", "oauth-client-secret"]
PEER_UNIT_SECRETS = ["ca-cert", "csr", "certificate", "private-key"]

# Timeouts
RESTART_TIMEOUT = 30
SERVICE_AVAILABLE_TIMEOUT = 90
REQUEST_TIMEOUT = 30

# Status messages
MSG_INSTALLING = "installing Opensearch Dashboards..."
MSG_STARTING = "starting..."
MSG_STARTING_SERVER = "starting Opensearch Dashboards server..."
MSG_WAITING_FOR_PEER = "waiting for peer relation"
MSG_STATUS_DB_MISSING = "Opensearch connection is missing"
MSG_STATUS_DB_DOWN = "Opensearch service is (partially or fully) down"
MSG_STATUS_DB_UNHEALTHY = "The OpenSearch service health is red"
MSG_TLS_CONFIG = "Waiting for TLS to be fully configured..."
MSG_INCOMPATIBLE_UPGRADE = "Incompatible Opensearch and Dashboards versions"
MSG_INVALID_CONFIG = "Config options invalid: "
MSG_STATUS_UNAVAIL = "Service unavailable"
MSG_STATUS_UNHEALTHY = "Service is not in a green health state"
MSG_STATUS_ERROR = "Service is an error state"
MSG_STATUS_WORKLOAD_DOWN = "Workload is not alive"
MSG_STATUS_UNKNOWN = "Workload status is not known"
MSG_STATUS_APP_REMOVED = "remove-application was requested: leaving..."
MSG_STATUS_HANGING = "Application does not respond, request hanging"
MSG_STATUS_OAUTH_INFO_FAILED = "Failed to get OAuth provider info from relation"

MSG_APP_STATUS = [
    MSG_STATUS_DB_DOWN,
    MSG_STATUS_DB_UNHEALTHY,
]

MSG_UNIT_STATUS = [
    MSG_STATUS_HANGING,
    MSG_STATUS_UNAVAIL,
    MSG_STATUS_UNHEALTHY,
    MSG_STATUS_WORKLOAD_DOWN,
    MSG_STATUS_UNKNOWN,
    MSG_STATUS_ERROR
]
