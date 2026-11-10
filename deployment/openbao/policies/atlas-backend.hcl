path "atlas/data/netbox/api" {
  capabilities = ["read"]
}

path "atlas/data/devices/credentials/*" {
  capabilities = ["read"]
}

path "atlas/data/zabbix/api" {
  capabilities = ["read"]
}

path "atlas/data/integrations/cisco/support-api" {
  capabilities = ["read"]
}

# P1 validates only KV metadata. Secret data is reserved for a future worker.
path "atlas/metadata/vpn/connections/*" {
  capabilities = ["read"]
}

# Authenticate Atlas administrators. Password hashes remain in OpenBao and are
# never materialized into the backend container filesystem.
path "atlas/data/auth/local/users/*" {
  capabilities = ["read"]
}
