path "atlas/data/netbox/api" {
  capabilities = ["read"]
}

path "atlas/data/devices/credentials/*" {
  capabilities = ["read"]
}

path "atlas/data/zabbix/api" {
  capabilities = ["read"]
}
