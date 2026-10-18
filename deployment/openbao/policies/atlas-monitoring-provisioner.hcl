# Create and reconcile only the SNMP leaf of a named device credential.
path "atlas/data/devices/credentials/+/+/snmp" {
  capabilities = ["create", "read", "update"]
}

path "atlas/metadata/devices/credentials/+/+/snmp" {
  capabilities = ["read"]
}

# Read-only compatibility permits one-time CAS migration by the application.
path "atlas/data/devices/snmp/*" {
  capabilities = ["read"]
}

path "atlas/metadata/devices/snmp/*" {
  capabilities = ["read"]
}
