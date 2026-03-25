
    @property
    def _user_pebble_layer(self) -> ops.pebble.Layer:
        """Returns a new layer to force services to run as _daemon_."""
        return ops.pebble.Layer(
            {
                "services": {
                    OSD_SERVICE: {
                        "override": "merge",
                        "user": "_daemon_",
                        "group": "_daemon_",
                    },
                    EXPORTER_SERVICE: {
                        "override": "merge",
                        "user": "_daemon_",
                        "group": "_daemon_",
                    },
                },
            }
        )
