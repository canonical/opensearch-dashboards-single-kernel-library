#!/usr/bin/env python3
# Copyright 2026 Canonical Ltd.
# See LICENSE file for licensing details.

from unittest.mock import DEFAULT, PropertyMock, patch

import pytest

from single_kernel_opensearch_dashboards.common.literals import (
    CERTS_REL_NAME,
    CHARM_KEY,
    PEERS_REL_NAME,
)


@pytest.mark.parametrize("harness", [{"add_upgrade": False}], indirect=True)
def test_certificates_joined_creates_private_key(harness):
    with (
        patch(
            "single_kernel_opensearch_dashboards.core.state.ClusterState.stable",
            new_callable=PropertyMock,
            return_value=True,
        ),
        patch(
            "single_kernel_opensearch_dashboards.workload.vm.VMWorkload.configure"
        ) as workload_config,
    ):
        cert_rel_id = harness.add_relation(CERTS_REL_NAME, "tls-certificates-operator")
        harness.add_relation_unit(cert_rel_id, "tls-certificates-operator/1")

    assert harness.charm.state.unit_server.private_key
    assert "BEGIN RSA PRIVATE KEY" in harness.charm.state.unit_server.private_key.splitlines()[0]
    assert workload_config.assert_called_once


@pytest.mark.parametrize("harness", [{"add_upgrade": False}], indirect=True)
def test_certificates_available_fails_wrong_csr(harness):
    cert_rel_id = harness.add_relation(CERTS_REL_NAME, "tls-certificates-operator")
    harness.update_relation_data(cert_rel_id, f"{CHARM_KEY}/0", {"csr": "not-missing"})

    harness.charm.tls_events.certificates.on.certificate_available.emit(
        certificate_signing_request="missing",
        certificate="cert",
        ca="ca",
        chain=["ca", "cert"],
    )

    assert not harness.charm.state.unit_server.certificate
    assert not harness.charm.state.unit_server.ca


@pytest.mark.parametrize("harness", [{"add_upgrade": False}], indirect=True)
def test_certificates_available_succeeds(harness):
    with harness.hooks_disabled():
        harness.add_relation(CERTS_REL_NAME, "tls-certificates-operator")

    # implicitly tests restart call
    harness.add_relation(harness.charm.restart_manager.name, "{CHARM_KEY}/0")

    harness.charm.unit.add_secret(
        {"csr": "not-missing"},
        label=f"{PEERS_REL_NAME}.opensearch-dashboards.unit",
    )

    # implicitly tests these method calls
    with patch.multiple(
        "single_kernel_opensearch_dashboards.managers.tls.TLSManager",
        set_private_key=DEFAULT,
        set_ca=DEFAULT,
        set_certificate=DEFAULT,
        # set_truststore=DEFAULT,
        # set_p12_keystore=DEFAULT,
    ):
        harness.charm.tls_events.certificates.on.certificate_available.emit(
            certificate_signing_request="not-missing",
            certificate="cert",
            ca="ca",
            chain=["ca", "cert"],
        )

        assert harness.charm.state.unit_server.certificate
        assert harness.charm.state.unit_server.ca


@pytest.mark.parametrize("harness", [{"add_upgrade": False}], indirect=True)
def test_certificates_broken(harness):
    with harness.hooks_disabled():
        certs_rel_id = harness.add_relation(CERTS_REL_NAME, "tls-certificates-operator")

        harness.charm.unit.add_secret(
            {
                "csr": "not-missing",
                "certificate": "cert",
                "ca-cert": "exists",
                "private-key": "key",
            },
            label=f"{PEERS_REL_NAME}.opensearch-dashboards.unit",
        )
        harness.set_leader(True)

    assert harness.charm.state.unit_server.certificate
    assert harness.charm.state.unit_server.ca
    assert harness.charm.state.unit_server.csr

    # implicitly tests these method calls
    with (
        patch.multiple(
            "single_kernel_opensearch_dashboards.managers.tls.TLSManager",
            remove_cert_files=DEFAULT,
            certificate_valid=lambda _: True,
        ),
        patch(
            "single_kernel_opensearch_dashboards.workload.vm.VMWorkload.configure"
        ) as workload_config,
        patch(
            "single_kernel_opensearch_dashboards.events.tls.TLSCertificatesRequiresV3.request_certificate_revocation"
        ) as request_revocation,
    ):
        harness.remove_relation(certs_rel_id)

        # While the TLS relation and certs are gone
        assert not harness.charm.state.unit_server.certificate
        assert not harness.charm.state.unit_server.ca
        assert not harness.charm.state.unit_server.csr
        assert not harness.charm.state.unit_server.tls_enabled
        request_revocation.assert_not_called()

        assert workload_config.assert_called_once


@pytest.mark.parametrize("harness", [{"add_upgrade": False}], indirect=True)
def test_certificates_expiring(harness):
    key = open("tests/keys/0.key").read()
    harness.update_relation_data(
        harness.charm.state.peer_relation.id,
        f"{CHARM_KEY}/0",
        {
            "csr": "csr",
            "private-key": key,
            "certificate": "cert",
            "hostname": "treebeard",
            "ip": "1.1.1.1",
            "fqdn": "fangorn",
        },
    )

    with (
        patch(
            "single_kernel_opensearch_dashboards.lib.charms.tls_certificates_interface.v3.tls_certificates.TLSCertificatesRequiresV3.request_certificate_renewal",
            return_value=None,
        ),
    ):
        harness.charm.tls_events.certificates.on.certificate_expiring.emit(
            certificate="cert", expiry=None
        )

        assert harness.charm.state.unit_server.csr != "csr"


@pytest.mark.parametrize("harness", [{"add_upgrade": False}], indirect=True)
def test_set_tls_private_key(harness):
    harness.update_relation_data(
        harness.charm.state.peer_relation.id,
        f"{CHARM_KEY}/0",
        {
            "csr": "csr",
            "private-key": "mellon",
            "certificate": "cert",
            "hostname": "treebeard",
            "ip": "1.1.1.1",
            "fqdn": "fangorn",
        },
    )
    key = open("tests/keys/0.key").read()

    with (
        patch(
            "single_kernel_opensearch_dashboards.lib.charms.tls_certificates_interface.v3.tls_certificates.TLSCertificatesRequiresV3.request_certificate_renewal",
            return_value=None,
        ),
    ):
        harness.run_action("set-tls-private-key", {"internal-key": key})

        assert harness.charm.state.unit_server.csr != "csr"
