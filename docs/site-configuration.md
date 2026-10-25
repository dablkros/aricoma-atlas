# Atlas first-boot site configuration

`atlasctl configure` creates the site-specific HTTPS ingress, TLS and monitoring
policy required before the first Atlas deployment. It replaces manual editing
of several runtime YAML files with one validated input and never stores the
input in Git.

## What the configurator owns

The command writes only these managed runtime targets:

```text
/opt/aricoma-atlas/.runtime/
├── components.yaml          # written by install/components add; not replaced here
├── inventory.yaml           # default customer and inventory import policy
├── proxy.yaml
├── tls/
│   ├── ca.crt
│   ├── netbox.crt
│   ├── netbox.key
│   ├── oxidized.crt
│   ├── oxidized.key
│   ├── zabbix.crt
│   └── zabbix.key
└── monitoring/
    ├── config.yaml
    └── trust/
        └── fortigate-ca.pem   # only when fortios.ca_bundle is configured
```

Deployment scripts continue to generate Docker Compose files, application
secrets, OpenBao identities and other service runtime data. The configurator
does not create DNS records, production certificates, device credentials or
OpenBao unseal keys.

## Fresh VM: interactive workflow

Install the selected, clean Git checkout first:

```console
sudo ./install.sh
sudo atlasctl configure
sudo atlasctl deploy
```

Prvá interaktívna inštalácia ponúkne Oxidized a Zabbix ako voliteľné
komponenty. Pevné jadro tvorí NetBox, OpenBao, Ansible a profylaxia. Pre
automatizovaný first boot použite napríklad
`sudo ./install.sh --components zabbix`; povolené hodnoty sú `oxidized`,
`zabbix`, ich čiarkou oddelená kombinácia alebo `none`.

Výber je monotónny: aktualizácia ho nemení a komponent sa dá neskôr iba pridať
cez `sudo atlasctl components add NAME`. Po pridaní treba cez `atlasctl
configure --force` doplniť hostname a TLS materiál a následne spustiť deploy.

The wizard asks for:

1. the specific Atlas management/listen IP;
2. HTTPS port and administrator CIDR networks;
3. a DNS suffix used to construct unique names for NetBox and selected optional services;
4. the exact Zabbix SNMP source IPv4 address when Zabbix is selected;
5. production TLS paths or an explicit LAB-only certificate mode;
6. an optional customer/Tenant name, deterministic slug and provisioning choice.

All values and TLS material are validated before a runtime configuration is
written. The three RFC1918 ranges are the default device target allowlist; use
a declarative site file when the customer policy should be narrower.

## Declarative and unattended workflow

Copy the versioned schema example outside the repository runtime and fill in
customer values:

```console
cp deployment/site.example.yaml /secure/path/customer-site.yaml
sudo atlasctl configure --site-file /secure/path/customer-site.yaml --check
sudo atlasctl configure --site-file /secure/path/customer-site.yaml
sudo atlasctl deploy
```

`--check` validates schema, IP addresses, networks, hostnames, file paths,
certificate/key matches, certificate validity, DNS SANs and the CA chain. It
does not write files. This mode can be used in image-building or CI before the
configuration is delivered to the appliance.

The same site file can be delivered through cloud-init, Packer, an ISO seed or
an infrastructure deployment pipeline. The file contains paths and policy but
must not contain private-key contents. Protect the file and referenced TLS
material according to the customer PKI process.

## Site schema version 1

### `inventory`

The optional `inventory.default_tenant` stores the first customer's name,
slug and whether deployment should provision it after the NetBox baseline.
Omitting the section preserves the existing workflow. Import policy also
defines the management interface name (`mgmt0`), default IPv4 prefix (`/32`),
default Device Role and explicit model aliases/role mappings. See
[Inventory import](inventory-import.md) for the complete contract.

### `proxy`

| Field | Required | Meaning |
|---|---:|---|
| `listen_address` | yes | Specific IPv4 or IPv6 address assigned to the Atlas VM. Wildcards and loopback are rejected. |
| `https_port` | no | HTTPS ingress port, default `443`. |
| `allowed_networks` | yes | Administrator/VPN CIDRs allowed to reach ingress. An address-family-wide `/0` is rejected. |

### `services`

`netbox` is always required. `oxidized` and `zabbix` are required only when
selected in `.runtime/components.yaml`; an unselected optional service must be
omitted. Each configured service needs a unique valid DNS hostname. In production TLS mode each service also declares
its certificate/full-chain and private-key path. One SAN certificate may be
referenced by all three services when it covers all names.

Relative TLS paths are resolved relative to `customer-site.yaml`, not the
current shell directory.

### `monitoring`

| Field | Required | Default/meaning |
|---|---:|---|
| `allowed_device_networks` | no | RFC1918 ranges; target management IP allowlist. |
| `source_ip` | conditionally | Compatibility form for one direct Zabbix egress IPv4. Mutually exclusive with `egress_profiles`. |
| `egress_profiles` | conditionally | Named target-network to source-IP mappings. Required for multiple egress paths. |
| `default_snmp_port` | no | `161`; non-Forti network platforms currently require 161. |
| `aruba_aoscx_vrf` | no | `default`. |
| `allow_unrestricted_snmp_v2c` | no | `false`; explicit exception for AOS-Switch only. |
| `proxy_name` | no | Compatibility form for one existing Zabbix proxy; with profiles it belongs inside each profile. |

`allowed_device_networks` and egress selection are different controls. The
former limits which NetBox primary addresses Atlas may contact. Egress profiles
select the expected SNMP source and optional existing Zabbix proxy by longest
matching target CIDR:

```yaml
monitoring:
  allowed_device_networks:
    - 10.0.0.0/8
    - 172.16.0.0/12
  egress_profiles:
    lab:
      device_networks:
        - 10.255.253.0/24
      source_ip: 10.99.98.200
    remote-site:
      device_networks:
        - 172.20.0.0/16
      source_ip: 172.20.0.5
      proxy_name: remote-site-proxy
```

Every profile network must be contained by `allowed_device_networks`. Duplicate
target CIDRs are rejected. Direct profiles may use different VM source
addresses: Zabbix leaves source selection to the operating system and Docker
SNAT, while Linux routing selects the address for each destination. Before
backend deployment, every direct profile is checked with `ip route get` on the
Atlas host. Deployment stops if Linux selects a different source address.
Proxy profiles are not checked against the Atlas VM route, because their SNMP
traffic originates on the external proxy.

Profile CIDRs must follow real routing boundaries. If a more-specific Linux
route selects another VM address inside a broad profile, add a narrower egress
profile for that destination; longest-prefix selection will prefer it.

The legacy single `source_ip` form remains valid for upgrades. It is checked
against every allowed device network and should be migrated to profiles when
the VM has multiple routing paths.

### `fortios`

The optional section controls the default VDOM, HTTPS/SNMP ports and TLS
verification. TLS verification defaults to enabled. Disabling it requires both
`tls_verify: false` and `lab_tls_exception: true`. A supplied `ca_bundle` is
copied to the protected monitoring trust directory and exposed to the backend
at `/run/atlas/monitoring/trust/fortigate-ca.pem`.

## TLS modes

### Production: `existing`

Use certificates issued by the customer PKI:

```yaml
tls:
  mode: existing
  ca_certificate: ./tls/customer-ca.crt
```

The command verifies every certificate and key pair with OpenSSL, including
current validity, server-auth purpose, hostname and CA chain. Invalid material
stops configuration before runtime YAML is changed.

### Isolated LAB only: `lab_generated`

```yaml
tls:
  mode: lab_generated
  validity_days: 30
```

Atlas creates a temporary CA and one SAN certificate covering all three service
hostnames. Validity is restricted to 1-90 days. Only the CA certificate and
service certificate/key are installed; the CA private key is destroyed with
the temporary staging directory. Consequently this mode cannot renew the same
certificate and must not be used as a production PKI.

LAB clients must explicitly trust the installed
`/opt/aricoma-atlas/.runtime/tls/ca.crt`. The generated SAN certificate covers
NetBox and the selected optional services. The command does not modify client
trust stores and does not bypass browser certificate warnings.

## Replacement and upgrade behavior

If any managed target already exists, the configurator stops without changing
it. Review the current and proposed site files, validate the proposal, then use
the explicit replacement flag:

```console
sudo atlasctl configure --site-file /secure/path/customer-site.yaml --check
sudo atlasctl configure --site-file /secure/path/customer-site.yaml --force
sudo atlasctl deploy
```

`--force` replaces only configurator-managed proxy, TLS and monitoring files.
It does not delete OpenBao data, databases, monitoring jobs or Docker volumes.
Symlinked runtime directories or managed target files are always rejected,
including with `--force`.

The deploy stage itself preserves an existing monitoring policy and does not
silently copy new defaults over it.

## Expected completion output

A successful run reports only non-secret destinations and selected addresses:

```text
[OK] Site configuration and TLS material validated
[INFO] Proxy listen address: 10.20.30.40:443
[INFO] Monitoring source IP: 10.20.30.40
[OK] Wrote protected proxy configuration: /opt/aricoma-atlas/.runtime/proxy.yaml
[OK] Wrote protected monitoring configuration: /opt/aricoma-atlas/.runtime/monitoring/config.yaml
[OK] Site configuration is ready; run sudo atlasctl deploy
```

Private-key contents are never printed. Runtime YAML and private keys use mode
`0600`; runtime and trust directories use `0700` and are owned by the Atlas
service identity.

## Troubleshooting

| Error | Meaning/action |
|---|---|
| `Site configuration version must be 1` | Start from the current `deployment/site.example.yaml`. |
| `services must contain exactly...` | Configure all three ingress services and remove unknown service keys. |
| `entire address family` | Replace `/0` with explicit administrator or management CIDRs. |
| `monitoring_egress_profile_unresolved` | Add a profile whose target CIDR contains the device management IP. |
| `monitoring_egress_profile_ambiguous` | Remove a duplicate target CIDR from competing profiles. |
| `expects source ... but the host route ... selects ...` | Correct the profile source IP or the Linux route before deployment. |
| certificate/key/hostname/CA validation failure | Reissue or correct the production certificate before deployment. |
| `already exists ... --force` | Validate the intended replacement first; use `--force` only after review. |
| `must not be a symlink` | Restore a regular protected runtime path; do not redirect security-sensitive files. |

After configuration, `sudo atlasctl deploy` remains the idempotent stack
reconciliation command. Use `sudo atlasctl status` and the component-specific
log commands for runtime diagnostics.
