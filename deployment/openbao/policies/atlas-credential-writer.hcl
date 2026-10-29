# Browser-approved device credential onboarding with one exact leaf per purpose.
path "atlas/data/devices/credentials/+/+/prophylaxis" {
  capabilities = ["create", "read", "update"]
}

path "atlas/data/devices/credentials/+/+/oxidized" {
  capabilities = ["create", "read", "update"]
}

path "atlas/data/devices/credentials/+/+/snmp" {
  capabilities = ["create", "read", "update"]
}
