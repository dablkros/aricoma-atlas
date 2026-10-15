"""Shared, fail-closed resolution from authoritative NetBox objects."""

from ipaddress import ip_interface


class DeviceResolutionError(RuntimeError):
    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


def resolve_tcp_service_port(services: object, default_port: int) -> int:
    """Resolve one exact NetBox TCP port mapping or use the platform default."""
    if not isinstance(services, list):
        raise DeviceResolutionError("invalid_service_port")
    if not services:
        return default_port
    if len(services) != 1:
        raise DeviceResolutionError("ambiguous_service_port")
    service = services[0]
    if not isinstance(service, dict):
        raise DeviceResolutionError("invalid_service_port")
    mappings = service.get("port_mappings")
    if not isinstance(mappings, list) or not mappings:
        raise DeviceResolutionError("invalid_service_port")
    if len(mappings) != 1:
        raise DeviceResolutionError("ambiguous_service_port")
    mapping = mappings[0]
    if not isinstance(mapping, str):
        raise DeviceResolutionError("invalid_service_port")
    protocol, separator, raw_port = mapping.partition("/")
    if protocol != "tcp" or separator != "/" or not raw_port.isdecimal():
        raise DeviceResolutionError("invalid_service_port")
    port = int(raw_port)
    if not 1 <= port <= 65535:
        raise DeviceResolutionError("invalid_service_port")
    return port


def resolve_primary_interface(netbox, raw_device: dict) -> str:
    """Resolve the primary IPv4's assigned dcim.interface and verify ownership."""
    primary = raw_device.get("primary_ip4") if isinstance(raw_device, dict) else None
    if not isinstance(primary, dict):
        raise DeviceResolutionError("management_interface_unresolved")
    address = primary.get("address")
    try:
        expected_ip = ip_interface(address).ip
    except (TypeError, ValueError):
        raise DeviceResolutionError("management_interface_unresolved") from None

    identifier = primary.get("id")
    if isinstance(identifier, int) and not isinstance(identifier, bool) and identifier > 0:
        ip_object = netbox.get(f"/api/ipam/ip-addresses/{identifier}/")
    else:
        matches = netbox.get_all("/api/ipam/ip-addresses/", params={"address": address})
        if len(matches) != 1:
            raise DeviceResolutionError("management_interface_unresolved")
        ip_object = matches[0]
    if not isinstance(ip_object, dict):
        raise DeviceResolutionError("management_interface_unresolved")
    try:
        actual_ip = ip_interface(ip_object.get("address")).ip
    except (TypeError, ValueError):
        raise DeviceResolutionError("management_interface_unresolved") from None
    if actual_ip != expected_ip or ip_object.get("assigned_object_type") != "dcim.interface":
        raise DeviceResolutionError("management_interface_unresolved")

    assigned = ip_object.get("assigned_object")
    interface_id = ip_object.get("assigned_object_id")
    if isinstance(assigned, dict):
        interface_id = assigned.get("id", interface_id)
    if not isinstance(interface_id, int) or isinstance(interface_id, bool) or interface_id < 1:
        raise DeviceResolutionError("management_interface_unresolved")
    interface = netbox.get(f"/api/dcim/interfaces/{interface_id}/")
    if not isinstance(interface, dict):
        raise DeviceResolutionError("management_interface_unresolved")
    owner = interface.get("device")
    device_id = raw_device.get("id")
    if not isinstance(owner, dict) or owner.get("id") != device_id:
        raise DeviceResolutionError("management_interface_unresolved")
    name = interface.get("name")
    if not isinstance(name, str) or not name.strip() or len(name) > 64:
        raise DeviceResolutionError("management_interface_unresolved")
    return name.strip()


__all__ = ["DeviceResolutionError", "resolve_primary_interface", "resolve_tcp_service_port"]
