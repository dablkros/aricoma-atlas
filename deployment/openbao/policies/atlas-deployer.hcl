# Read and write Atlas KV v2 secrets.
path "atlas/data/*" {
  capabilities = ["create", "read", "update", "patch"]
}

# Read metadata and list Atlas secrets.
path "atlas/metadata/*" {
  capabilities = ["read", "list"]
}

path "atlas/metadata" {
  capabilities = ["list"]
}
