# Allow the Atlas operator helper to run the quorum-protected
# root token generation workflow.

path "sys/generate-root-token/attempt" {
  capabilities = ["create", "read", "update", "delete", "sudo"]
}

path "sys/generate-root-token/update" {
  capabilities = ["create", "update", "sudo"]
}
