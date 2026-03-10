#!/usr/bin/env python3
# Copyright 2026 Canonical Ltd.
# See LICENSE file for licensing details.

"""Collection of global cluster state."""
import logging
from ipaddress import IPv4Address, IPv6Address
from typing import Optional, Set

from ops.framework import Object
from ops.model import Relation, Unit

from single_kernel_opensearch_dashboards.common.literals import (
    CERTS_REL_NAME,
    DASHBOARD_INDEX,
    DASHBOARD_ROLE,
    JWT_REL_NAME,
    OAUTH_REL_NAME,
    OPENSEARCH_REL_NAME,
    PEER_APP_SECRETS,
    PEER_UNIT_SECRETS,
    PEERS_REL_NAME,
    SERVER_PORT,
    UPGRADE_REL_NAME,
    Substrates,
)
from single_kernel_opensearch_dashboards.core.config import CharmConfig
from single_kernel_opensearch_dashboards.core.models import (
    JwtConfigurationRequires,
    OAuth,
    OpensearchServer,
    OSDCluster,
    OSDServer,
)
from single_kernel_opensearch_dashboards.lib.charms.data_platform_libs.v0.data_interfaces import (
    DataPeerData,
    DataPeerOtherUnitData,
    DataPeerUnitData,
    OpenSearchRequiresData,
)
from single_kernel_opensearch_dashboards.lib.charms.data_platform_libs.v1.data_models import (
    TypedCharmBase,
)

logger = logging.getLogger(__name__)


class ClusterState(Object):
    """Collection of global cluster state for Framework/Object."""

    def __init__(
        self,
        charm: TypedCharmBase[CharmConfig],
        substrate: Substrates,
    ):
        super().__init__(parent=charm, key="osd_charm_state")
        self.substrate = substrate
        self.charm = charm
        self._servers_data = {}

        self.peer_app_data = DataPeerData(
            self.model, relation_name=PEERS_REL_NAME, additional_secret_fields=PEER_APP_SECRETS
        )
        self.peer_unit_data = DataPeerUnitData(
            self.model, relation_name=PEERS_REL_NAME, additional_secret_fields=PEER_UNIT_SECRETS
        )
        self.client_requires_data = OpenSearchRequiresData(
            self.model,
            relation_name=OPENSEARCH_REL_NAME,
            index=DASHBOARD_INDEX,
            extra_user_roles=DASHBOARD_ROLE,
        )
        self.jwt_requires = JwtConfigurationRequires(self.model, relation_name=JWT_REL_NAME)

    # --- RAW RELATION ---

    @property
    def peer_relation(self) -> Relation | None:
        """The cluster peer relation."""
        return self.model.get_relation(PEERS_REL_NAME)

    @property
    def upgrade_relation(self) -> Relation | None:
        """The cluster upgrade relation."""
        return self.model.get_relation(UPGRADE_REL_NAME)

    @property
    def opensearch_relation(self) -> Relation | None:
        """The Opensearch Server relation."""
        return self.model.get_relation(OPENSEARCH_REL_NAME)

    @property
    def tls_relation(self) -> Relation | None:
        """The cluster tls relation."""
        return self.model.get_relation(CERTS_REL_NAME)

    @property
    def oauth_relation(self) -> Relation | None:
        """The cluster Oauth relation."""
        return self.model.get_relation(OAUTH_REL_NAME)

    @property
    def jwt_relation(self) -> Relation | None:
        """Return the jwt relation if present."""
        return self.jwt_requires.relations[0] if len(self.jwt_requires.relations) else None

    # --- CORE COMPONENTS---
    @property
    def unit(self) -> Unit:
        """Unit that this execution is responsible for."""
        return self.charm.unit

    @property
    def unit_server(self) -> OSDServer:
        """The server state of the current running Unit."""
        return OSDServer(
            relation=self.peer_relation,
            data_interface=self.peer_unit_data,
            component=self.model.unit,
            substrate=self.substrate,
        )

    @property
    def peer_units_data(self) -> dict[Unit, DataPeerOtherUnitData]:
        """The cluster peer relation."""
        if not self.peer_relation or not self.peer_relation.units:
            return {}

        for unit in self.peer_relation.units:
            if unit not in self._servers_data:
                self._servers_data[unit] = DataPeerOtherUnitData(
                    model=self.model, unit=unit, relation_name=PEERS_REL_NAME
                )
        return self._servers_data

    @property
    def cluster(self) -> OSDCluster:
        """The cluster state of the current running App."""
        return OSDCluster(
            relation=self.peer_relation,
            data_interface=self.peer_app_data,
            component=self.model.app,
            substrate=self.substrate,
            tls=bool(self.tls_relation),
        )

    @property
    def servers(self) -> set[OSDServer]:
        """Grabs all servers in the current peer relation, including the running unit server.

        Returns:
            Set of ODServers in the current peer relation, including the running unit server.
        """
        if not self.peer_relation:
            return set()

        servers = set()
        for unit, data_interface in self.peer_units_data.items():
            servers.add(
                OSDServer(
                    relation=self.peer_relation,
                    data_interface=data_interface,
                    component=unit,
                    substrate=self.substrate,
                )
            )
        servers.add(self.unit_server)

        return servers

    @property
    def opensearch_server(self) -> OpensearchServer | None:
        """The state for all related client Applications."""
        if not self.opensearch_relation or not self.opensearch_relation.app:
            return None

        # We assume no more than 1 server relation
        return OpensearchServer(
            relation=self.opensearch_relation,
            data_interface=self.client_requires_data,
            component=self.opensearch_relation.app,
            substrate=self.substrate,
            local_app=self.cluster.app,
        )

    @property
    def oauth(self) -> OAuth:
        """The oauth relation state."""
        return OAuth(
            relation=self.oauth_relation,
            client_secret=self.cluster.oauth_client_secret,
        )

    @property
    def bind_address(self) -> IPv4Address | IPv6Address | str | None:
        """The network binding address from the peer relation."""
        bind_address = None
        if self.peer_relation:
            if binding := self.model.get_binding(self.peer_relation):
                bind_address = binding.network.bind_address
        # If the relation does not exist, then we get None
        return bind_address

    # --- CLUSTER INIT ---

    @property
    def all_units_related(self) -> bool:
        """Checks if currently related units make up all planned units.

        Returns:
            True if all units are related. Otherwise False
        """
        return len(self.servers) == self.model.app.planned_units()

    # --- HEALTH ---

    @property
    def stable(self) -> bool:
        """Flag to check if the quorum is in a stable state, with all members up-to-date."""
        if not self.all_units_related:
            logger.debug("cluster not stable - not all units related")
            return False

        return True

    @property
    def url(self) -> str:
        """Service URL."""
        scheme = "https" if self.unit_server.tls else "http"
        return f"{scheme}://{self.bind_address}:{SERVER_PORT}"

    # --- UPGRADE RELATED ---
    @property
    def upgrade_unit_states(self) -> list:
        """Current upgrade state for all units.

        Returns:
            Unsorted list of upgrade states for all units.
        """
        if not self.upgrade_relation:
            return []

        return [
            self.upgrade_relation.data[unit].get("state", "") for unit in self.upgrade_app_units
        ]

    @property
    def upgrade_idle(self) -> Optional[bool]:
        """Flag for whether the cluster is in an idle upgrade state.

        Returns:
            True if all application units in idle state. Otherwise False
        """
        return set(self.upgrade_unit_states) == {"idle"}

    @property
    def upgrade_app_units(self) -> Set[Unit]:
        """The peer-related units in the application."""
        if not self.upgrade_relation:
            return set()

        return set([self.model.unit] + list(self.upgrade_relation.units))
