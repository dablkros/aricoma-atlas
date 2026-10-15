# Monitoring provisioning P0

Atlas reconciles a NetBox-selected FortiGate into SNMPv2c monitoring in
Zabbix. `monitor_only` reads an existing device-specific SNMP secret and changes
only the Atlas-owned Zabbix host. `provision_and_monitor` also reconciles the
FortiGate SNMP agent, one Atlas-owned community, and SNMP access on the
configured management interface.

No write endpoint accepts a management address, API token, SNMP community,
template, or proxy from the caller. Those values come from NetBox, OpenBao,
`catalog/zabbix.yaml`, and the root-owned monitoring configuration.

## Deployment

Fresh OpenBao bootstrap creates a separate
`atlas-monitoring-provisioner` AppRole. On an existing installation run:

```console
sudo atlasctl openbao
# Select: 2. Reconcile Atlas backend roles
```

The role reads device management credentials and creates, reads, or updates
only `atlas/devices/snmp/*`. It cannot delete secrets or access deployment
secrets.

Backend deployment creates `.runtime/monitoring/config.yaml` from a safe
default, `.runtime/monitoring/operator-token`, and a persistent SQLite job
directory. The default permits only TEST-NET-1 and has no device entries. Build
the customer configuration from `deployment/monitoring.example.yaml`, then
redeploy the backend.

Keep customer CA files under `.runtime/monitoring/trust/` and use their
container path, for example `/run/atlas/monitoring/trust/fortigate-ca.pem`. TLS verification is
required by default. A per-device `tls_verify: false` is accepted only together
with `lab_tls_exception: true`.

Read the UI bearer token on the appliance:

```console
sudo cat .runtime/monitoring/operator-token
```

The browser keeps it only in memory. It is required for every
`/api/monitoring/*` request.

## Safe operation

1. In NetBox set `monitoring_enabled=true`, active status, manufacturer
   `fortinet`, platform `fortios`, primary management IPv4, site, and
   `credential_profile`.
2. Store the FortiOS API token in the existing device credential profile.
3. Add the NetBox device ID, management interface, VDOM, explicit monitoring
   source IPs, ports, TLS settings, and optional Zabbix proxy to the runtime
   monitoring configuration.
4. In the Atlas UI enter the operator token, select a mode, and inspect.
5. Review and confirm the plan. Plans expire after 15 minutes and are rejected
   if inspected state changes.
6. Follow the job until `COMPLETED`, `FAILED`, or `PARTIAL`. Verification
   `PENDING` means Zabbix has not returned a fresh successful `sysObjectID`.

Atlas never removes customer SNMP communities. A community conflict, unmanaged
Zabbix host collision, unexpected schema, or unsupported version stops the
operation. An interrupted write job becomes `PARTIAL` and requires a new plan.

## API sequence

All requests use `Authorization: Bearer <operator-token>`.

```text
GET  /api/monitoring/devices
POST /api/monitoring/devices/{id}/inspect
POST /api/monitoring/devices/{id}/plan
POST /api/monitoring/devices/{id}/provision
POST /api/monitoring/devices/{id}/reconcile
POST /api/monitoring/provision/bulk
GET  /api/monitoring/jobs/{job_id}
```

Write requests require a server-issued `plan_id` and `confirmed: true`.

## Validated contracts and limits

P0 accepts FortiOS `7.0.9` and Zabbix `7.0.x` only. The FortiGate field
contract was checked against Fortinet's `fortinet.fortios` 2.6.0
`fortios_system_snmp_community` module. Live FortiOS 7.0.9 schema response and
host mask behavior still require a lab integration test before production;
static tests cannot verify an appliance API.

Primary references:

- FortiOS 7.0.9 CLI reference:
  <https://docs.fortinet.com/document/fortigate/7.0.9/cli-reference/708841/cli-configuration-commands>
- Fortinet Ansible collection 2.6.0:
  <https://github.com/fortinet-ansible-dev/ansible-galaxy-fortios-collection/tree/2.6.0>
- Zabbix 7.0 secret user macros:
  <https://www.zabbix.com/documentation/7.0/en/manual/api/reference/usermacro/object>
- Zabbix 7.0 host interfaces:
  <https://www.zabbix.com/documentation/7.0/en/manual/api/reference/hostinterface/object>
