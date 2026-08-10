#!/usr/bin/env python3
# Copyright 2026 Canonical Ltd.
# See LICENSE file for licensing details.

"""Backward-compatibility tests for the `opensearch-client` relation."""

import json

import pytest

from single_kernel_opensearch_dashboards.common.literals import (
    CHARM_KEY,
    DASHBOARD_INDEX,
    DASHBOARD_ROLE,
    OPENSEARCH_REL_NAME,
)
from single_kernel_opensearch_dashboards.core.opensearch import OpensearchServer

OPENSEARCH_APP_NAME = "opensearch"

PASSWORD = "s3cr3t"
TLS_CA = "<ca-cert-data>"
# Intentionally unsorted: `.endpoints` is expected to sort them.
ENDPOINTS = "10.0.0.2:9200,10.0.0.1:9200"
SORTED_ENDPOINTS = ["10.0.0.1:9200", "10.0.0.2:9200"]
VERSION = "2.13.0"


def _user_secret(harness):
    """The `user` secret group carries `password` in data-interfaces v1."""
    uri = harness.add_model_secret(OPENSEARCH_APP_NAME, {"password": PASSWORD})
    harness.grant_secret(uri, CHARM_KEY)
    return uri


def _tls_secret(harness):
    """The `tls` secret group carries `tls-ca` in data-interfaces v1."""
    uri = harness.add_model_secret(OPENSEARCH_APP_NAME, {"tls-ca": TLS_CA})
    harness.grant_secret(uri, CHARM_KEY)
    return uri


def _write_v0(harness, relation_id):
    """Publish a flat data-interfaces v0 provider databag."""
    databag = {
        "secret-user": _user_secret(harness),
        "secret-tls": _tls_secret(harness),
        "endpoints": ENDPOINTS,
        "version": VERSION,
    }
    with harness.hooks_disabled():
        harness.update_relation_data(relation_id, OPENSEARCH_APP_NAME, databag)


def _write_v1(harness, relation_id):
    """Publish a data-interfaces v1 provider databag."""
    request = {
        "resource": "dashboards-index",
        "endpoints": ENDPOINTS,
        "version": VERSION,
        "secret-user": _user_secret(harness),
        "secret-tls": _tls_secret(harness),
    }
    databag = {"version": "v1", "requests": json.dumps([request])}
    with harness.hooks_disabled():
        harness.update_relation_data(relation_id, OPENSEARCH_APP_NAME, databag)


@pytest.mark.parametrize("write_databag", [_write_v0, _write_v1], ids=["v0", "v1"])
def test_opensearch_server_reads_both_contract_versions(harness, write_databag):
    """OpensearchServer exposes an identical surface for v0 and v1 providers."""
    relation_id = harness.add_relation(OPENSEARCH_REL_NAME, OPENSEARCH_APP_NAME)
    harness.add_relation_unit(relation_id, f"{OPENSEARCH_APP_NAME}/0")

    write_databag(harness, relation_id)

    relation = harness.model.get_relation(OPENSEARCH_REL_NAME, relation_id)
    server = OpensearchServer.from_relation(harness.model, relation)

    assert server is not None
    assert server.password == PASSWORD
    assert server.tls_ca == TLS_CA
    assert server.version == VERSION
    assert server.endpoints == SORTED_ENDPOINTS


def test_opensearch_server_none_without_relation(harness):
    """No relation (or no remote app) yields no server rather than an error."""
    assert OpensearchServer.from_relation(harness.model, None) is None


def test_requirer_advertises_both_v0_and_v1_request(harness):
    """The requirer must advertise the request in both contract shapes."""
    with harness.hooks_disabled():
        harness.set_leader(True)

    relation_id = harness.add_relation(OPENSEARCH_REL_NAME, OPENSEARCH_APP_NAME)

    databag = harness.get_relation_data(relation_id, CHARM_KEY)
    # Flat v0 request fields — what a v0 opensearch provider reads.
    assert databag.get("index") == DASHBOARD_INDEX
    assert databag.get("extra-user-roles") == DASHBOARD_ROLE
    # v1 request still advertised for a v1 provider.
    assert databag.get("version") == "v1"
    assert databag.get("requests")


def test_requirer_does_not_advertise_when_not_leader(harness):
    """Only the leader writes the request databag (matches the parent's leader gate)."""
    relation_id = harness.add_relation(OPENSEARCH_REL_NAME, OPENSEARCH_APP_NAME)
    databag = harness.get_relation_data(relation_id, CHARM_KEY)
    assert "index" not in databag


def test_v0_provider_relation_changed_does_not_fault_hook(harness, mocker):
    """A real relation-changed for a flat v0 provider must not error the hook."""
    # Isolate the requirer handler from the charm's own restart plumbing.
    mocker.patch(
        "single_kernel_opensearch_dashboards.charms.base.OpenSearchDashboardsBaseCharm"
        ".pre_restart_check",
        return_value=False,
    )

    relation_id = harness.add_relation(OPENSEARCH_REL_NAME, OPENSEARCH_APP_NAME)
    harness.add_relation_unit(relation_id, f"{OPENSEARCH_APP_NAME}/0")

    databag = {
        "secret-user": _user_secret(harness),
        "secret-tls": _tls_secret(harness),
        "endpoints": ENDPOINTS,
        "version": VERSION,
    }
    # Hooks ENABLED: this fires relation_changed through the requirer handler.
    # It must complete without raising.
    harness.update_relation_data(relation_id, OPENSEARCH_APP_NAME, databag)

    relation = harness.model.get_relation(OPENSEARCH_REL_NAME, relation_id)
    server = OpensearchServer.from_relation(harness.model, relation)
    assert server is not None
    assert server.version == VERSION
    assert server.password == PASSWORD


@pytest.mark.parametrize("write_databag", [_write_v0, _write_v1], ids=["v0", "v1"])
def test_opensearch_server_none_on_invalid_databag(harness, write_databag, mocker):
    """A databag that fails validation is swallowed (logged) and returns no server."""
    relation_id = harness.add_relation(OPENSEARCH_REL_NAME, OPENSEARCH_APP_NAME)
    harness.add_relation_unit(relation_id, f"{OPENSEARCH_APP_NAME}/0")
    write_databag(harness, relation_id)

    # Corrupt the v1 requests payload / make the v0 databag invalid.
    with harness.hooks_disabled():
        harness.update_relation_data(relation_id, OPENSEARCH_APP_NAME, {"requests": "not-json"})

    relation = harness.model.get_relation(OPENSEARCH_REL_NAME, relation_id)
    # v0 stays valid (it ignores `requests`); only v1 is broken by this.
    server = OpensearchServer.from_relation(harness.model, relation)
    if write_databag is _write_v1:
        assert server is None
    else:
        assert server is not None
