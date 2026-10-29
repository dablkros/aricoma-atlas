# Read and write Atlas KV v2 secrets.
path "atlas/data/*" {
  capabilities = ["create", "read", "update", "patch"]
}

# A recovery password is written once during administrator bootstrap. The
# deployer can create it, but cannot read, update, patch or delete it.
path "atlas/data/auth/recovery/users/*" {
  capabilities = ["create"]
}

# Read metadata and list Atlas secrets.
path "atlas/metadata/*" {
  capabilities = ["read", "list"]
}

path "atlas/metadata" {
  capabilities = ["list"]
}
