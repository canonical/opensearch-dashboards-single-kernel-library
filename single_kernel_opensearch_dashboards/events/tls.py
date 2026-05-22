#!/usr/bin/env python3
# Copyright 2026 Canonical Ltd.
# See LICENSE file for licensing details.

"""Event handler for related applications on the `certificates` relation interface."""
import base64
import logging
import re
from subprocess import CalledProcessError
from typing import Any, cast

from ops import Object
from ops.charm import ActionEvent, CharmBase, RelationDepartedEvent
from ops.framework import EventBase

from single_kernel_opensearch_dashboards.charms.charm_status import StatusHandlingCharm
from single_kernel_opensearch_dashboards.common.exceptions import (
    OSDFileOperationError,
    OSDTLSMissingDataError,
)
from single_kernel_opensearch_dashboards.common.literals import (
    CERTS_REL_NAME,
    SERVER_PORT,
    Substrates,
)
from single_kernel_opensearch_dashboards.core.cluster import ClusterState
from single_kernel_opensearch_dashboards.core.statuses import TLSStatuses
from single_kernel_opensearch_dashboards.lib.charms.tls_certificates_interface.v3.tls_certificates import (
    CertificateAvailableEvent,
    TLSCertificatesRequiresV3,
    generate_csr,
    generate_private_key,
)
from single_kernel_opensearch_dashboards.managers.ingres import IngressManager
from single_kernel_opensearch_dashboards.managers.tls import TLSManager

logger = logging.getLogger(__name__)


class TLSEvents(Object):
    """Event handlers for related applications on the `certificates` relation interface."""

    def __init__(
        self,
        charm: StatusHandlingCharm,
        state: ClusterState,
        tls_manager: TLSManager,
        ingress_manager: IngressManager,
    ) -> None:
        super().__init__(charm, "tls")  # type: ignore[arg-type]
        self.charm = charm
        self.state = state
        self.tls_manager = tls_manager
        self.ingress_manager = ingress_manager
        self.certificates = TLSCertificatesRequiresV3(
            cast(CharmBase, cast(Any, charm)), CERTS_REL_NAME
        )

        self.framework.observe(
            self.charm.on[CERTS_REL_NAME].relation_created, self._on_certs_relation_created
        )
        self.framework.observe(
            self.charm.on[CERTS_REL_NAME].relation_broken, self._on_certs_relation_broken
        )
        self.framework.observe(
            self.charm.on[CERTS_REL_NAME].relation_departed, self._on_client_departed
        )
        self.framework.observe(
            self.certificates.on.certificate_available, self._on_certificate_available
        )
        self.framework.observe(
            self.certificates.on.certificate_expiring, self._on_certificate_expiring
        )

        self.framework.observe(self.charm.on.set_tls_private_key_action, self._set_tls_private_key)
        self.framework.observe(self.charm.on.config_changed, self._on_config_changed)

    def _request_certificates(self, event: EventBase) -> None:
        """Request brand-new certificates."""
        if self.state.unit_server.tls_enabled:
            self._remove_certificates(event)

        csr = self.tls_manager.generate_csr()

        self.state.unit_server.update({"csr": csr.decode("utf-8").strip()})
        self.certificates.request_certificate_creation(certificate_signing_request=csr)

        self.charm.status_handler.set_running_status(
            TLSStatuses.WAITING_FOR_TLS.value,
            scope="unit",
            statuses_state=self.state.statuses,
            component_name=self.tls_manager.name,
        )

    def _remove_certificates(self, event: EventBase) -> None:
        """Cleanup any existing certificates."""
        if self.state.cluster.tls_enabled and self.state.unit_server.csr:
            self.certificates.request_certificate_revocation(
                self.state.unit_server.csr.encode("utf-8")
            )
            self.charm.status_handler.set_running_status(
                TLSStatuses.WAITING_FOR_TLS.value,
                scope="unit",
                statuses_state=self.state.statuses,
                component_name=self.tls_manager.name,
            )

        self.state.unit_server.update({"csr": "", "certificate": "", "ca-cert": ""})

        try:
            self.tls_manager.remove_cert_files()
        except CalledProcessError as e:
            logger.warning(f"Failed to remove cert files: {e}. Deferring.")
            event.defer()
            return

    def _on_certs_relation_created(self, event: EventBase) -> None:
        """Handler for `certificates_relation_created` event."""
        self._request_certificates(event)

    def _on_certificate_available(self, event: "CertificateAvailableEvent") -> None:
        """Handler for `certificates_available` event after provider updates signed certs."""
        self.state.delete_status_if_present(
            TLSStatuses.WAITING_FOR_TLS.value, scope="unit", component=self.tls_manager.name
        )

        if event.certificate_signing_request != self.state.unit_server.csr:
            logger.error("Can't use certificate, found unknown CSR")
            return

        self.state.unit_server.update({"certificate": event.certificate, "ca-cert": event.ca})
        if self.state.substrate == Substrates.K8S and self.state.ingress_relation:
            self.state.ingress_requirer.provide_ingress_requirements(
                scheme="https", port=SERVER_PORT
            )
            logger.info("Updated ingress relation to use HTTPS scheme.")
        try:
            self.tls_manager.set_private_key()
            self.tls_manager.set_ca()
            self.tls_manager.set_certificate()
        except OSDTLSMissingDataError as e:
            logger.warning(f"TLS data incomplete: {e}. Deferring event.")
            event.defer()
            return
        except OSDFileOperationError as e:
            logger.error(f"Operation with files is failed: {e}. Deferring event.")
            event.defer()
            return

    def _on_certificate_expiring(self, event: EventBase) -> None:
        """Handler for `certificates_expiring` event when certs need renewing."""
        if not (self.state.unit_server.private_key or self.state.unit_server.csr):
            logger.warning("Missing unit private key and/or old csr. Deferring.")
            event.defer()
            return

        new_csr = generate_csr(
            private_key=self.state.unit_server.private_key.encode("utf-8"),
            subject=self.state.unit_server.host,
            sans_ip=self.state.unit_server.sans.get("sans_ip"),
            sans_dns=self.state.unit_server.sans.get("sans_dns"),
        )

        self.certificates.request_certificate_renewal(
            old_certificate_signing_request=self.state.unit_server.csr.encode("utf-8"),
            new_certificate_signing_request=new_csr,
        )

        self.charm.status_handler.set_running_status(
            TLSStatuses.WAITING_FOR_TLS.value,
            scope="unit",
            statuses_state=self.state.statuses,
            component_name=self.tls_manager.name,
        )

        self.state.unit_server.update({"csr": new_csr.decode("utf-8").strip()})

    def _on_config_changed(self, event: EventBase) -> None:
        """If system configuration (such as IP) changes, certs have to be re-issued."""
        if self.state.unit_server.tls_enabled and not self.tls_manager.certificate_valid():
            self._remove_certificates(event)
            self._request_certificates(event)

    def _on_certs_relation_broken(self, event: EventBase) -> None:
        """Handler for `certificates_relation_broken` event."""
        # In case we have valid certificates, we keep them for smooth service function
        if self.state.unit_server.unit_dying:
            return

        if self.state.substrate == Substrates.K8S and self.state.ingress_relation:
            self.state.ingress_requirer.provide_ingress_requirements(
                scheme="http", port=SERVER_PORT
            )
            logger.info("Updated ingress relation to use HTTP scheme.")
        self._remove_certificates(event)

    def _set_tls_private_key(self, event: "ActionEvent") -> None:
        """Handler for `set-tls-private-key` event when user manually specifies private-keys for a unit."""
        key = event.params.get("internal-key") or generate_private_key().decode("utf-8")
        private_key = (
            key
            if re.match(r"(-+(BEGIN|END) [A-Z ]+-+)", key)
            else base64.b64decode(key).decode("utf-8")
        )

        self.state.unit_server.update({"private-key": private_key})
        self._on_certificate_expiring(event)

    def _on_client_departed(self, event: RelationDepartedEvent) -> None:
        """Handle unit dying."""
        if event.departing_unit == self.charm.unit:
            self.state.unit_server.unit_dying = True
