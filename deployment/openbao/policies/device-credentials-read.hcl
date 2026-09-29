path "atlas/data/devices/*" {
  capabilities = ["read"]
}

path "atlas/metadata/devices/*" {
  capabilities = ["read", "list"]
}

path "atlas/metadata/devices" {
  capabilities = ["list"]
}
