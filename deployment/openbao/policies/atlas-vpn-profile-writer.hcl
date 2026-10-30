# VPN onboarding writes only an exact connection leaf and never lists secrets.
path "atlas/data/vpn/connections/+" {
  capabilities = ["create", "read", "update"]
}
