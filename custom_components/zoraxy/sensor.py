"""Sensoren."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.const import (
    PERCENTAGE,
    EntityCategory,
    UnitOfInformation,
    UnitOfTime,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import ZoraxyConfigEntry
from .coordinator import ZoraxyCoordinator, ZoraxyData, parse_zoraxy_time
from .entity import ZoraxyEntity, ZoraxyHostEntity, track_items

PARALLEL_UPDATES = 0


def _overview(key: str) -> Callable[[ZoraxyData], Any]:
    return lambda d: (d.overview or {}).get(key)


def _system(key: str) -> Callable[[ZoraxyData], Any]:
    return lambda d: (d.system or {}).get(key)


def _started(data: ZoraxyData) -> datetime | None:
    seconds = (data.overview or {}).get("SystemUptime")
    if not isinstance(seconds, int | float):
        return None
    # Auf volle Minuten runden, damit der Zeitstempel nicht bei jedem Abruf springt.
    started = data.fetched_at - timedelta(seconds=seconds)
    return started.replace(second=0, microsecond=0)


def _round(value: Any, digits: int = 1) -> Any:
    return round(value, digits) if isinstance(value, int | float) else None


@dataclass(frozen=True, kw_only=True)
class ServerSensorDescription(SensorEntityDescription):
    value_fn: Callable[[ZoraxyData], Any]
    exists_fn: Callable[[ZoraxyData], bool] = lambda _: True


SERVER_SENSORS: tuple[ServerSensorDescription, ...] = (
    ServerSensorDescription(
        key="requests_today",
        translation_key="requests_today",
        state_class=SensorStateClass.TOTAL_INCREASING,
        value_fn=_overview("TotalRequestToday"),
        exists_fn=lambda d: d.overview is not None,
    ),
    ServerSensorDescription(
        key="errors_today",
        translation_key="errors_today",
        state_class=SensorStateClass.TOTAL_INCREASING,
        value_fn=_overview("ErrorRequestToday"),
        exists_fn=lambda d: d.overview is not None,
    ),
    ServerSensorDescription(
        key="valid_today",
        translation_key="valid_today",
        state_class=SensorStateClass.TOTAL_INCREASING,
        entity_registry_enabled_default=False,
        value_fn=_overview("ValidRequestToday"),
        exists_fn=lambda d: d.overview is not None,
    ),
    ServerSensorDescription(
        key="visitors_today",
        translation_key="visitors_today",
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=_overview("UniqueVisitorsToday"),
        exists_fn=lambda d: "UniqueVisitorsToday" in (d.overview or {}),
    ),
    ServerSensorDescription(
        key="received_today",
        translation_key="received_today",
        device_class=SensorDeviceClass.DATA_SIZE,
        state_class=SensorStateClass.TOTAL_INCREASING,
        native_unit_of_measurement=UnitOfInformation.BYTES,
        suggested_unit_of_measurement=UnitOfInformation.MEGABYTES,
        suggested_display_precision=1,
        value_fn=_overview("BandwidthRxToday"),
        exists_fn=lambda d: "BandwidthRxToday" in (d.overview or {}),
    ),
    ServerSensorDescription(
        key="sent_today",
        translation_key="sent_today",
        device_class=SensorDeviceClass.DATA_SIZE,
        state_class=SensorStateClass.TOTAL_INCREASING,
        native_unit_of_measurement=UnitOfInformation.BYTES,
        suggested_unit_of_measurement=UnitOfInformation.MEGABYTES,
        suggested_display_precision=1,
        value_fn=_overview("BandwidthTxToday"),
        exists_fn=lambda d: "BandwidthTxToday" in (d.overview or {}),
    ),
    ServerSensorDescription(
        key="active_connections",
        translation_key="active_connections",
        state_class=SensorStateClass.MEASUREMENT,
        # Zoraxy meldet -1, wenn es die Verbindungen nicht zählen kann.
        value_fn=lambda d: (
            v
            if isinstance(v := (d.overview or {}).get("ActiveConnections"), int) and v >= 0
            else None
        ),
        exists_fn=lambda d: "ActiveConnections" in (d.overview or {}),
    ),
    ServerSensorDescription(
        key="proxy_hosts",
        translation_key="proxy_hosts",
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda d: len(d.hosts),
    ),
    ServerSensorDescription(
        key="expired_certificates",
        translation_key="expired_certificates",
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda d: len(d.expired_domains),
    ),
    ServerSensorDescription(
        key="started",
        translation_key="started",
        device_class=SensorDeviceClass.TIMESTAMP,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=_started,
        exists_fn=lambda d: "SystemUptime" in (d.overview or {}),
    ),
    ServerSensorDescription(
        key="version",
        translation_key="version",
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda d: d.version,
    ),
    ServerSensorDescription(
        key="cpu",
        translation_key="cpu",
        native_unit_of_measurement=PERCENTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda d: _round(_system("CPUUsage")(d)),
        exists_fn=lambda d: d.system is not None,
    ),
    ServerSensorDescription(
        key="memory",
        translation_key="memory",
        native_unit_of_measurement=PERCENTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda d: _round(_system("RAMUsage")(d)),
        exists_fn=lambda d: d.system is not None,
    ),
    ServerSensorDescription(
        key="disk",
        translation_key="disk",
        native_unit_of_measurement=PERCENTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda d: _round(_system("DiskUsage")(d)),
        exists_fn=lambda d: d.system is not None,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ZoraxyConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    coordinator = entry.runtime_data.coordinator
    async_add_entities(
        ServerSensor(coordinator, entry, desc)
        for desc in SERVER_SENSORS
        if desc.exists_fn(coordinator.data)
    )
    track_items(
        coordinator,
        entry,
        async_add_entities,
        lambda d: d.certificates.keys(),
        lambda domain: [CertificateSensor(coordinator, entry, domain)],
    )
    track_items(
        coordinator,
        entry,
        async_add_entities,
        lambda d: d.hosts.keys(),
        lambda domain: [HostSensor(coordinator, entry, domain, desc) for desc in HOST_SENSORS],
    )


class ServerSensor(ZoraxyEntity, SensorEntity):
    entity_description: ServerSensorDescription

    def __init__(
        self,
        coordinator: ZoraxyCoordinator,
        entry: ZoraxyConfigEntry,
        description: ServerSensorDescription,
    ) -> None:
        super().__init__(coordinator, entry, description.key)
        self.entity_description = description

    @property
    def native_value(self) -> Any:
        return self.entity_description.value_fn(self.coordinator.data)

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        if self.entity_description.key == "expired_certificates":
            return {"domains": self.coordinator.data.expired_domains}
        return None


class CertificateSensor(ZoraxyEntity, SensorEntity):
    """Ablaufdatum eines Zertifikats im Zoraxy-Zertifikatsspeicher."""

    _attr_translation_key = "certificate"
    _attr_device_class = SensorDeviceClass.TIMESTAMP

    def __init__(self, coordinator: ZoraxyCoordinator, entry: ZoraxyConfigEntry, domain: str):
        super().__init__(coordinator, entry, f"certificate_{domain}")
        self.cert_domain = domain
        self._attr_translation_placeholders = {"name": domain}

    @property
    def _cert(self) -> dict[str, Any]:
        return self.coordinator.data.certificates.get(self.cert_domain, {})

    @property
    def available(self) -> bool:
        return super().available and self.cert_domain in self.coordinator.data.certificates

    @property
    def native_value(self) -> datetime | None:
        return parse_zoraxy_time(self._cert.get("ExpireDate"))

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        cert = self._cert
        return {
            "remaining_days": cert.get("RemainingDays"),
            "renewed": cert.get("LastModifiedDate"),
            "dns_challenge": cert.get("UseDNS"),
            "fallback": cert.get("IsFallback"),
        }


@dataclass(frozen=True, kw_only=True)
class HostSensorDescription(SensorEntityDescription):
    value_fn: Callable[[ZoraxyData, str, dict[str, Any]], Any]


def _uptime(attr: str) -> Callable[[ZoraxyData, str, dict[str, Any]], Any]:
    def _get(data: ZoraxyData, domain: str, _host: dict[str, Any]) -> Any:
        uptime = data.uptime.get(domain)
        return getattr(uptime, attr) if uptime else None

    return _get


def _upstreams(_data: ZoraxyData, _domain: str, host: dict[str, Any]) -> str | None:
    origins = [o.get("OriginIpOrDomain") for o in host.get("ActiveOrigins") or []]
    return ", ".join(o for o in origins if o) or None


HOST_SENSORS: tuple[HostSensorDescription, ...] = (
    HostSensorDescription(
        key="latency",
        translation_key="latency",
        native_unit_of_measurement=UnitOfTime.MILLISECONDS,
        device_class=SensorDeviceClass.DURATION,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=_uptime("latency"),
    ),
    HostSensorDescription(
        key="availability",
        translation_key="availability",
        native_unit_of_measurement=PERCENTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=_uptime("availability"),
    ),
    HostSensorDescription(
        key="upstream",
        translation_key="upstream",
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=_upstreams,
    ),
    HostSensorDescription(
        key="status_code",
        translation_key="status_code",
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
        value_fn=_uptime("status_code"),
    ),
    HostSensorDescription(
        key="last_check",
        translation_key="last_check",
        device_class=SensorDeviceClass.TIMESTAMP,
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
        value_fn=_uptime("checked"),
    ),
)


class HostSensor(ZoraxyHostEntity, SensorEntity):
    entity_description: HostSensorDescription

    def __init__(
        self,
        coordinator: ZoraxyCoordinator,
        entry: ZoraxyConfigEntry,
        domain: str,
        description: HostSensorDescription,
    ) -> None:
        super().__init__(coordinator, entry, domain, description.key)
        self.entity_description = description

    @property
    def native_value(self) -> Any:
        return self.entity_description.value_fn(self.coordinator.data, self.domain, self.host)
