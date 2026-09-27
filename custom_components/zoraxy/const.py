"""Konstanten der Zoraxy-Integration."""

from __future__ import annotations

from typing import Final

DOMAIN: Final = "zoraxy"

CONF_SCAN_INTERVAL: Final = "scan_interval"
CONF_UPTIME_INTERVAL: Final = "uptime_interval"

# Steuerfunktionen – jede einzeln über die Optionen freischaltbar.
CONF_CONTROL_HOSTS: Final = "control_hosts"
CONF_CONTROL_PROXY: Final = "control_proxy"
CONF_CONTROL_ACCESS: Final = "control_access"
CONF_CONTROL_STREAMS: Final = "control_streams"
CONF_CONTROL_REDIRECTS: Final = "control_redirects"
CONF_CONTROL_CERTIFICATES: Final = "control_certificates"

CONTROL_OPTIONS: Final = (
    CONF_CONTROL_HOSTS,
    CONF_CONTROL_PROXY,
    CONF_CONTROL_ACCESS,
    CONF_CONTROL_STREAMS,
    CONF_CONTROL_REDIRECTS,
    CONF_CONTROL_CERTIFICATES,
)

DEFAULT_PORT: Final = 8000
DEFAULT_SCAN_INTERVAL: Final = 60
MIN_SCAN_INTERVAL: Final = 15
MAX_SCAN_INTERVAL: Final = 900
# Zoraxy prüft Upstreams alle 5 Minuten; die Antwort enthält den Verlauf
# eines ganzen Tages und ist entsprechend groß.
DEFAULT_UPTIME_INTERVAL: Final = 300
MIN_UPTIME_INTERVAL: Final = 60
MAX_UPTIME_INTERVAL: Final = 3600
# Version und Host-ID ändern sich selten; /api/proxy/status ist groß.
STATUS_INTERVAL: Final = 1800
