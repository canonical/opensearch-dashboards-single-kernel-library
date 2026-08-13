#!/usr/bin/env python3
# Copyright 2026 Canonical Ltd.
# See LICENSE file for licensing details.

"""Collection of global cluster state."""

import logging
from typing import Literal

from data_platform_helpers.advanced_statuses import StatusesState, StatusObject
from data_platform_helpers.advanced_statuses.protocol import StatusesStateProtocol
from ops.framework import Object
from ops.model import ModelError, Relation, Unit

from single_kernel_opensearch_dashboards.common.literals import (
    CERTS_REL_NAME,
    DASHBOARD_INDEX,
    DASHBOARD_ROLE,
    INGRESS_REL_NAME,
    JWT_REL_NAME,
    OAUTH_REL_NAME,
    OPENSEARCH_REL_NAME,
    PEERS_REL_NAME,
    SERVER_PORT,
    STATUS_PEERS_REL_NAME,
    UPGRADE_REL_NAME,
    Substrates,
)
from single_kernel_opensearch_dashboards.core.config import CharmConfig
from single_kernel_opensearch_dashboards.core.ingress import IngressModel
from single_kernel_opensearch_dashboards.core.jwt import JWTAuthConfiguration
from single_kernel_opensearch_dashboards.core.network import Network
from single_kernel_opensearch_dashboards.core.oauth import OAuthModel
from single_kernel_opensearch_dashboards.core.opensearch import OpensearchServer
from single_kernel_opensearch_dashboards.core.peer_app import OSDClusterModel
from single_kernel_opensearch_dashboards.core.peer_unit import OSDServerModel
from single_kernel_opensearch_dashboards.core.relation_base import bind_model_to_repository
from single_kernel_opensearch_dashboards.core.upgrades import UpgradeUnitModel
from single_kernel_opensearch_dashboards.lib.charms.data_platform_libs.v1.data_interfaces import (
    OpsOtherPeerUnitRepositoryInterface,
    OpsPeerRepositoryInterface,
    OpsPeerUnitRepositoryInterface,
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

    def __init__(
        self,
        charm: TypedCharmBase[CharmConfig],
        substrate: Substrates,
    ):
        super().__init__(parent=charm, key="osd_charm_state")
        self.substrate = substrate
        self.charm = charm
        # In-memory flag, set for the remainder of the `stop` hook dispatch so that
        # status recomputation ( emitted by ops right after `stop`)
        # can skip live health checks against a workload
        # that is already being torn down.
        self.unit_stopping = False

        # Repository interfaces for the peer databags (models built lazily below).
        self.peer_app_interface = OpsPeerRepositoryInterface(
            model=self.model, relation_name=PEERS_REL_NAME, data_model=OSDClusterModel
        )
        self.peer_unit_interface = OpsPeerUnitRepositoryInterface(
            model=self.model, relation_name=PEERS_REL_NAME, data_model=OSDServerModel
        )

        self.statuses = StatusesState(self, STATUS_PEERS_REL_NAME)

    def _bind(
        self, interface, relation: Relation | None, model_cls, component, read_only: bool = False
    ):
        """Build a fresh model bound to its relation databag.

        Every access rebuilds and rebinds, so a model always reflects the current databag
        """
        if not relation:
            return model_cls()
        return bind_model_to_repository(
            interface, relation.id, model_cls, component, read_only=read_only
        )

    # --- RELATIONS ---

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
        return self.model.get_relation(JWT_REL_NAME)

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
    def unit_server(self) -> OSDServerModel:
        """The server state of the current running Unit."""
        return self._bind(
            self.peer_unit_interface, self.peer_relation, OSDServerModel, self.model.unit
        )

    @property
    def network(self) -> Network:
        """Host/network address resolution for the current running Unit."""
        return Network(self.model.unit, self.substrate, self.bind_address)

    @property
    def cluster(self) -> OSDClusterModel:
        """The cluster state of the current running App."""
        return self._bind(
            self.peer_app_interface, self.peer_relation, OSDClusterModel, self.model.app
        )

    @property
    def servers(self) -> list[OSDServerModel]:
        """Grabs all servers in the current peer relation, including the running unit server."""
        if not self.peer_relation:
            return []

        servers: list[OSDServerModel] = []
        for unit in self.peer_relation.units:
            # The running unit is added separately below
            if unit == self.model.unit:
                continue
            interface = OpsOtherPeerUnitRepositoryInterface(
                model=self.model,
                relation_name=PEERS_REL_NAME,
                unit=unit,
                data_model=OSDServerModel,
            )
            model = self._bind(interface, self.peer_relation, OSDServerModel, unit, read_only=True)
            servers.append(model)
        servers.append(self.unit_server)

        return servers

    @property
    def opensearch_server(self) -> OpensearchServer | None:
        """The state for the related OpenSearch server Application."""
        return OpensearchServer.from_relation(self.model, self.opensearch_relation)

    @property
    def jwt(self) -> JWTAuthConfiguration | None:
        """JWT configuration published by the provider on the JWT relation, if any."""
        return JWTAuthConfiguration.from_relation(self.model, self.jwt_relation)

    @property
    def ingress(self) -> IngressModel:
        """The ingress relation state."""
        return IngressModel.from_relation(self.ingress_relation)

    @property
    def bind_address(self) -> str | None:
        """The network binding address from the peer relation."""
        if not (relation := self.peer_relation):
            return None

        if not (binding := self.model.get_binding(relation)):
            return None

        if (address := binding.network.bind_address) is None:
            return None

        return f"{address}"

    # --- OAUTH ---
    @property
    def oauth(self) -> OAuthModel:
        """The oauth relation state."""
        return OAuthModel.from_relation(
            relation=self.oauth_relation,
            client_secret=self.cluster.oauth_client_secret or "",
        )

    @property
    def oauth_require(self) -> OAuthRequirer:
        """The oauth relation state."""
        return OAuthRequirer(self.charm, self.oauth_client_config(), relation_name=OAUTH_REL_NAME)

    def oauth_client_config(self) -> ClientConfig:
        """Generates actual client config for the OAuth."""
        return OAuthModel.client_config(self.oauth_url)

    # --- CLUSTER INIT ---

    @property
    def all_units_related(self) -> bool:
        """Checks if currently related units make up all planned units.

        Returns:
            True if all units are related. Otherwise False
        """
        try:
            planned = self.model.app.planned_units()
        except ModelError:
            return True
        return len(self.servers) == planned

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
        if self.substrate == Substrates.VM:
            return f"{scheme}://{self.bind_address}:{SERVER_PORT}"

        if self.ingress_relation and self.ingress.url:
            return f"{scheme}://{self.network.host}:{SERVER_PORT}{self.ingress.base_path}"

        return f"{scheme}://{self.network.host}:{SERVER_PORT}"

    @property
    def oauth_url(self) -> str:
        """Oauth URL for redirection"""
        if self.ingress_relation and self.ingress.url:
            return self.ingress.url

        return self.url

    @property
    def app_removal(self) -> bool:
        """Whether the whole application is going down."""
        try:
            return self.charm.app.planned_units() == 0
        except ModelError:
            # juju check planned units for charm using `goal-state` for all model
            # `goal-state` can fail to resolve the full model state (e.g. a cross-model
            # relation's remote offer is already gone), even though we only care about
            # our own app. Assume the app is going down because that can happen only if model is being destroyed.
            return True

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
            UpgradeUnitModel.from_relation(self.upgrade_relation, unit).state
            for unit in self.upgrade_app_units
        ]

    @property
    def upgrade_idle(self) -> bool:
        """Flag for whether the cluster is in an idle upgrade state.

        Returns:
            True if all application units in idle state. Otherwise False
        """
        return UpgradeUnitModel.all_idle(self.upgrade_unit_states)

    @property
    def upgrade_app_units(self) -> set[Unit]:
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

        for scope in target_scopes:
            if scope == "app" and not self.unit.is_leader():
                continue

            current_statuses = self.statuses.get(scope=scope, component=component)
            if status in current_statuses:
                self.statuses.delete(
                    status=status,
                    scope=scope,
                    component=component,
                )

    def add_status_to_both(self, status: StatusObject, component: str) -> None:
        """Adds status to both app and unit

        Checks if unit is leader, if not sets status only for unit

        Args:
            status (StatusObject): The status object to remove.
            component (str): The name of the component holding the status.
        """
        statuses = self.statuses.get("unit", component=component)
        if status not in statuses:
            self.statuses.add(
                status=status,
                scope="unit",
                component=component,
            )
        if self.unit.is_leader():
            statuses = self.statuses.get("app", component=component)
            if status not in statuses:
                self.statuses.add(
                    status=status,
                    scope="app",
                    component=component,
                )
