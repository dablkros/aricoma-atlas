# Read device management credentials needed by vendor drivers.
path "atlas/data/devices/credentials/*" {
  capabilities = ["read"]
}

# Create and reconcile only device-specific monitoring credentials.
path "atlas/data/devices/snmp/*" {
  capabilities = ["create", "read", "update"]
}

path "atlas/metadata/devices/snmp/*" {
  capabilities = ["read"]
}
