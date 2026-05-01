#!/usr/bin/env python3
# Copyright 2026 Canonical Ltd.
# See LICENSE file for licensing details.

"""Collection of global cluster state."""
import logging
from typing import Literal, Optional, Set

from data_platform_helpers.advanced_statuses import StatusesState, StatusObject
from data_platform_helpers.advanced_statuses.protocol import StatusesStateProtocol
from ops import StoredState
from ops.framework import Object
from ops.model import Relation, Unit

from single_kernel_opensearch_dashboards.common.literals import (
    CERTS_REL_NAME,
    DASHBOARD_INDEX,
    DASHBOARD_ROLE,
    INGRESS_REL_NAME,
    JWT_REL_NAME,
    OAUTH_REL_NAME,
    OPENSEARCH_REL_NAME,
    PEER_APP_SECRETS,
    PEER_UNIT_SECRETS,
    PEERS_REL_NAME,
    SERVER_PORT,
    STATUS_PEERS_REL_NAME,
    UPGRADE_REL_NAME,
    Substrates,
)
from single_kernel_opensearch_dashboards.core.config import CharmConfig
from single_kernel_opensearch_dashboards.core.models import (
    JWT,
    Ingress,
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
from single_kernel_opensearch_dashboards.lib.charms.hydra.v0.oauth import (
    ClientConfig,
    OAuthRequirer,
)

logger = logging.getLogger(__name__)


class ClusterState(Object, StatusesStateProtocol):
    """Collection of global cluster state for Framework/Object."""
    _stored_state = StoredState()

    def __init__(
        self,
        charm: TypedCharmBase[CharmConfig],
        substrate: Substrates,
    ):
        super().__init__(parent=charm, key="osd_charm_state")
        self.substrate = substrate
        self.charm = charm
        self._stored_state.set_default(unit_dying=False)
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

        self.statuses = StatusesState(self, STATUS_PEERS_REL_NAME)

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
        return self.jwt.jwt_relation

    @property
    def ingress_relation(self) -> Relation | None:
        """Return the ingress relation if present."""
        if self.substrate == Substrates.VM:
            return None
        return self.model.get_relation(INGRESS_REL_NAME)

    # --- CORE COMPONENTS---
    @property
    def unit(self) -> Unit:
        """Unit that this execution is responsible for."""
        return self.charm.unit

    @property
    def config(self) -> CharmConfig:
        """Config of a charm"""
        return self.charm.config

    @property
    def unit_server(self) -> OSDServer:
        """The server state of the current running Unit."""
        return OSDServer(
            relation=self.peer_relation,
            data_interface=self.peer_unit_data,
            component=self.model.unit,
            substrate=self.substrate,
            _stored_state= self._stored_state,
            bind_address=self.bind_address,
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
                    _stored_state=self._stored_state,
                    bind_address=self.bind_address,
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
    def jwt(self) -> JWT:
        """The jwt relation state."""
        return JWT(model=self.model, relation_name=JWT_REL_NAME)

    @property
    def ingress(self) -> Ingress:
        """The ingress relation state."""
        return Ingress(relation=self.ingress_relation)

    @property
    def bind_address(self) -> str | None:
        """The network binding address from the peer relation."""
        if not self.peer_relation:
            return None

        if not (binding := self.model.get_binding(self.peer_relation)):
            return None

        return str(binding.network.bind_address)

    # --- OAUTH ---
    @property
    def oauth(self) -> OAuth:
        """The oauth relation state."""
        return OAuth(
            relation=self.oauth_relation,
            client_secret=self.cluster.oauth_client_secret,
        )

    @property
    def oauth_require(self) -> OAuthRequirer:
        """The oauth relation state."""
        return OAuthRequirer(self.charm, self.oauth_client_config(), relation_name=OAUTH_REL_NAME)

    def oauth_client_config(self) -> ClientConfig:
        """Generates actual client config for the OAuth."""
        return ClientConfig(
            audience=["opensearch"],
            redirect_uri=f"{self.oauth_url}/auth/openid/login",
            scope="openid profile email phone offline address",
            grant_types=["authorization_code"],
            token_endpoint_auth_method="client_secret_post",
        )

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
        scheme = "https" if self.unit_server.tls_enabled else "http"
        if self.substrate != Substrates.K8S:
            return f"{scheme}://{self.bind_address}:{SERVER_PORT}"
        else:
            if self.ingress_relation and self.ingress.url:
                return f"{scheme}://{self.unit_server.host}:{SERVER_PORT}/{self.ingress.base_path}"

            return f"{scheme}://{self.unit_server.host}:{SERVER_PORT}"

    @property
    def oauth_url(self) -> str:
        scheme = "https" if self.unit_server.tls_enabled else "http"
        if self.ingress and self.ingress.url:
            return self.ingress.url
        elif self.substrate == Substrates.VM:
            return self.url
        else:
            return f"{scheme}://127.0.0.1:{SERVER_PORT}"

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

    # --- STATUS ---
    def delete_status_if_present(
        self, status: StatusObject, scope: Literal["unit", "app", "both"], component: str
    ) -> None:
        """Delete a status from a specific component safely.

        Checks if the status actually exists in the current state to avoid
        logging unnecessary warnings when attempting to delete a non-existent status.

        Args:
            status (StatusObject): The status object to remove.
            scope (Literal["unit", "app"]): The scope from which to remove the status.
            component (str): The name of the component holding the status.
        """
        target_scopes: list[Literal["unit", "app"]] = (
            ["unit", "app"] if scope == "both" else [scope]
        )

        for s in target_scopes:
            if s == "app" and not self.unit.is_leader():
                continue

            current_statuses = self.statuses.get(scope=s, component=component)
            if status in current_statuses:
                self.statuses.delete(
                    status=status,
                    scope=s,
                    component=component,
                )

    def add_status_to_both(self, status: StatusObject, component: str) -> None:
        """Adds status to both app and unit

        Checks if unit is leader, if not sets status only for unit

        Args:
            status (StatusObject): The status object to remove.
            component (str): The name of the component holding the status.
        """

        self.statuses.add(
            status=status,
            scope="unit",
            component=component,
        )
        if self.unit.is_leader():
            self.statuses.add(
                status=status,
                scope="app",
                component=component,
            )
