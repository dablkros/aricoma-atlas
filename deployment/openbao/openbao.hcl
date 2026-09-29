ui = true

storage "raft" {
  path    = "/openbao/file"
  node_id = "atlas-openbao-1"
}

listener "tcp" {
  address         = "0.0.0.0:8200"
  cluster_address = "0.0.0.0:8201"
  tls_disable     = true
}

api_addr     = "http://127.0.0.1:18200"
cluster_addr = "http://127.0.0.1:8201"

audit "file" "atlas-audit" {
  description = "Aricoma Atlas OpenBao audit log"

  options {
    file_path = "stdout"
  }
}