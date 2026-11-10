# The isolated worker reads only one explicitly requested VPN connection leaf.
# It cannot list, create, update, delete, or read metadata for other secret trees.
path "atlas/data/vpn/connections/+" {
  capabilities = ["read"]
}
