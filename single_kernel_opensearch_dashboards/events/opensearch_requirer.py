#!/usr/bin/env python3
# Copyright 2026 Canonical Ltd.
# See LICENSE file for licensing details.

"""Event handler for related applications on the `opensearch-client` relation interface."""

import json
import logging
from typing import cast

from dpcharmlibs.interfaces import (
    OpsRelationRepository,
    RequirerCommonModel,
    ResourceProviderModel,
    ResourceRequirerEventHandler,
)
from ops import CharmBase, Object
from ops.charm import (
    RelationBrokenEvent,
    RelationChangedEvent,
    RelationCreatedEvent,
    RelationEvent,
    SecretChangedEvent,
)
from ops.model import Application, Relation
from typing_extensions import Any

from single_kernel_opensearch_dashboards.charms.charm_status import StatusHandlingCharm
from single_kernel_opensearch_dashboards.common.exceptions import OSDFileOperationError
from single_kernel_opensearch_dashboards.common.literals import (
    CLUSTER_MANAGER_NAME,
    DASHBOARD_INDEX,
    DASHBOARD_ROLE,
    OPENSEARCH_REL_NAME,
)
from single_kernel_opensearch_dashboards.common.statuses import ServerStatuses
from single_kernel_opensearch_dashboards.core.state import ClusterState

logger = logging.getLogger(__name__)


# TODO: drop in favour of ResourceRequirerEventHandler when opensearch updates to di v1
class V0CompatibleResourceRequirer(ResourceRequirerEventHandler):
    """Requirer handler that also speaks to a legacy data-interfaces v0 OpenSearch provider.

    Support is bidirectional:

    - Request: the parent only advertises the v1 ``requests`` envelope, which a v0 provider
      can't parse, so it would never create the index/user. The request is mirrored in the
      flat v0 shape (``index``/``extra-user-roles``/``extra-group-roles``, plus
      ``requested-secrets`` so credentials come through Juju secrets); a v1 provider
      ignores those keys.
    - Response: the v1 relation-changed / secret-changed handlers build a ``DataContractV1``
      from the provider databag and raise ``ValidationError`` on a flat v0 one (its
      ``version`` is the OpenSearch *workload* version, not ``"v1"``), faulting the hook.
      They are skipped for a v0 provider; its response is read through ``OpensearchServer``
      in ``RequirerEvents._on_client_relation_changed``.
    """

    # Fields a v0 requirer asks to receive through Juju secrets (v0 ``RequirerData``'s
    # ``SECRET_FIELDS``); without ``requested-secrets`` a v0 provider publishes them in plaintext.
    V0_REQUESTED_SECRETS = [
        "username",
        "password",
        "tls",
        "tls-ca",
        "uris",
        "read-only-uris",
        "entity-name",
        "entity-password",
    ]

    def _on_relation_created_event(self, event: RelationCreatedEvent) -> None:
        super()._on_relation_created_event(event)

        if not self.charm.unit.is_leader():
            return

        repository = OpsRelationRepository(self.model, event.relation, self.charm.app)
        repository.write_field("requested-secrets", json.dumps(self.V0_REQUESTED_SECRETS))
        for request in self._requests:
            if request.resource:
                repository.write_field("index", request.resource)
            if request.extra_user_roles:
                repository.write_field("extra-user-roles", request.extra_user_roles)
            if request.extra_group_roles:
                repository.write_field("extra-group-roles", request.extra_group_roles)

    def _provider_speaks_v0(self, relation: Relation, app: Application | None) -> bool:
        """True when the provider's databag is a flat, pre-v1 payload with data present."""
        if app is None:
            return False
        version = OpsRelationRepository(self.model, relation, component=app).get_field("version")
        return version is not None and version != "v1"

    def _on_relation_changed_event(self, event: RelationChangedEvent) -> None:
        if self._provider_speaks_v0(event.relation, event.app):
            logger.debug("v0 opensearch provider detected; skipping v1 requirer processing.")
            return
        super()._on_relation_changed_event(event)

    def _on_secret_changed_event(self, event: SecretChangedEvent) -> None:
        if event.secret.label:
            relation = self._relation_from_secret_label(event.secret.label)
            if relation and self._provider_speaks_v0(relation, relation.app):
                logger.debug("v0 opensearch provider detected; skipping v1 secret processing.")
                return
        super()._on_secret_changed_event(event)


class RequirerEvents(Object):
    """Event handlers for related applications on the `opensearch-client` relation interface."""

    def __init__(
        self,
        charm: StatusHandlingCharm,
        state: ClusterState,
    ) -> None:
        super().__init__(charm, "provider")  # type: ignore[arg-type]
        self.charm = charm
        self.state = state
        self.tls_manager = self.charm.tls_manager
        self.requirer_events = V0CompatibleResourceRequirer(
            cast(CharmBase, cast(Any, charm)),
            relation_name=OPENSEARCH_REL_NAME,
            requests=[
                RequirerCommonModel(resource=DASHBOARD_INDEX, extra_user_roles=DASHBOARD_ROLE),
            ],
            response_model=ResourceProviderModel,
        )
        self.framework.observe(
            self.charm.on[OPENSEARCH_REL_NAME].relation_changed, self._on_client_relation_changed
        )
        self.framework.observe(
            self.charm.on[OPENSEARCH_REL_NAME].relation_broken, self._on_client_relation_broken
        )

    def _on_client_relation_changed(self, event: RelationEvent) -> None:
        """Updates ACLs while handling `client_relation_changed` events."""
        if not self.state.stable:
            event.defer()
            return

        if not self.charm.pre_restart_check():
            event.defer()
            return

        # The opensearch fills the databag only after index creation;
        # don't restart until it's finished, another relation-changed
        # will fire once the index is created
        server = self.state.opensearch_server
        if not (server and server.password and server.endpoints and server.tls_ca):
            logger.debug("OpenSearch relation data incomplete, not restarting")
            return

        try:
            self.tls_manager.set_ca_opensearch()
            self.charm.emit_restart(event)
        except OSDFileOperationError as e:
            logger.error(f"Operation with files is failed: {e}. Deferring event.")
            event.defer()
            return

    def _on_client_relation_broken(self, event: RelationBrokenEvent) -> None:
        """Restoring config to defaults if the relation is gone.

        Args:
            event: used for passing `RelationBrokenEvent` to subsequent methods
        """
        # do not bother reconfiguring/restarting a unit that is going down anyway
        if self.charm.is_app_removal(event):
            return

        if not self.charm.pre_restart_check():
            event.defer()
            return

        self.state.add_status_to_both(
            status=ServerStatuses.DB_CONNECTION_MISSING.value,
            component=CLUSTER_MANAGER_NAME,
        )

        if self.tls_manager.remove_ca_opensearch():
            event.defer()
            return

        self.charm.emit_restart(event)
