from data_platform_helpers.advanced_statuses import StatusObject
from data_platform_helpers.advanced_statuses.types import Scope

from single_kernel_opensearch_dashboards.common.literals import SERVER_PORT, Substrates
from single_kernel_opensearch_dashboards.core.cluster import ClusterState
from single_kernel_opensearch_dashboards.core.config import CharmConfig
from single_kernel_opensearch_dashboards.core.statuses import (
    CharmStatuses,
    ServerStatuses,
)
from single_kernel_opensearch_dashboards.lib.charms.data_platform_libs.v1.data_models import (
    TypedCharmBase,
)
from single_kernel_opensearch_dashboards.lib.charms.traefik_k8s.v2.ingress import (
    IngressPerAppRequirer,
)
from single_kernel_opensearch_dashboards.managers.base import BaseManager
from single_kernel_opensearch_dashboards.workload.base import WorkloadBase


class IngressManager(BaseManager):
    """Ingress manager for k8s."""

    def __init__(
        self, charm: TypedCharmBase[CharmConfig], state: ClusterState, workload: WorkloadBase
    ) -> None:
        super().__init__(state, workload)
        self.charm = charm
        self.name = "ingress_manager"

        if self.state.substrate == Substrates.VM:
            return

        # not really a manager, but we'll see how to best adjust that once we add HAProxy
        self.ingress = IngressPerAppRequirer(
            charm,
            port=SERVER_PORT,
            scheme="https" if self.state.cluster.tls_enabled else "http",
            strip_prefix=False,  # for the time being, while we support full load balancing
        )

    def get_statuses(self, scope: Scope, recompute: bool = False) -> list[StatusObject]:
        """Compute the ingress manager's statuses."""
        if not recompute:
            statuses = self.state.statuses.get(scope, self.name).root
            return statuses or [CharmStatuses.ACTIVE_IDLE.value]

        if self.state.substrate == Substrates.VM:
            return [CharmStatuses.ACTIVE_IDLE.value]

        if not self.ingress.relation:
            return [ServerStatuses.INGRESS_RELATION_MISSING.value]

        if not self.ingress.is_ready():
            return [ServerStatuses.INGRESS_RELATION_NOT_READY.value]

        return [CharmStatuses.ACTIVE_IDLE.value]
