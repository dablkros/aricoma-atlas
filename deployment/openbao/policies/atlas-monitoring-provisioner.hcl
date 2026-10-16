# Create and reconcile only device-specific monitoring credentials.
path "atlas/data/devices/snmp/*" {
  capabilities = ["create", "read", "update"]
}

path "atlas/metadata/devices/snmp/*" {
  capabilities = ["read"]
}
