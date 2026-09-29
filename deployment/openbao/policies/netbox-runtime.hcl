path "atlas/data/netbox/*" {
  capabilities = ["read"]
}

path "atlas/metadata/netbox" {
  capabilities = ["read", "list"]
}

path "atlas/metadata/netbox/*" {
  capabilities = ["read", "list"]
}
