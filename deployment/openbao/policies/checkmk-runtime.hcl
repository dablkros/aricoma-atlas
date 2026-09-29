path "atlas/data/checkmk/*" {
  capabilities = ["read"]
}

path "atlas/metadata/checkmk" {
  capabilities = ["read", "list"]
}

path "atlas/metadata/checkmk/*" {
  capabilities = ["read", "list"]
}
