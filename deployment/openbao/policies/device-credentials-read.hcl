path "atlas/data/devices/credentials/*" {
  capabilities = ["read"]
}

path "atlas/metadata/devices/credentials/*" {
  capabilities = ["read", "list"]
}

path "atlas/metadata/devices" {
  capabilities = ["list"]
}

path "atlas/metadata/devices/credentials" {
  capabilities = ["list"]
}
