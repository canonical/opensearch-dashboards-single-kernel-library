#!/usr/bin/env python3
# Copyright 2024 Canonical Ltd.
# See LICENSE file for licensing details.

import logging

from ops.main import main
from ops.log import JujuLogHandler

from single_kernel_opensearch_dashboards.charms.vm import OpenSearchVMCharm

# Show logger name (module name) in logs
root_logger = logging.getLogger()
for handler in root_logger.handlers:
    if isinstance(handler, JujuLogHandler):
        handler.setFormatter(logging.Formatter("{name}:{message}", style="{"))
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)

if __name__ == "__main__":
    main(OpenSearchVMCharm)