path "atlas/data/oxidized/*" {
  capabilities = ["read"]
}

path "atlas/metadata/oxidized" {
  capabilities = ["read", "list"]
}

path "atlas/metadata/oxidized/*" {
  capabilities = ["read", "list"]
}
