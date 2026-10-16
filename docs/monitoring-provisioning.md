# Monitoring provisioning P0

Atlas reconciles a NetBox-selected FortiGate into SNMPv2c monitoring in
Zabbix. `monitor_only` reads an existing device-specific SNMP secret and changes
only the Atlas-owned Zabbix host. `provision_and_monitor` also reconciles the
FortiGate SNMP agent, one Atlas-owned community, and SNMP access on the
NetBox-resolved management interface.

No write endpoint accepts a management address, API token, SNMP community,
template, or proxy from the caller. Those values come from NetBox, OpenBao,
`catalog/zabbix.yaml`, and the root-owned site policy.

NetBox owns device identity, primary IPv4, platform, monitoring selection, and
`credential_profile`. OpenBao owns operator-managed API credentials and the
Atlas-generated device SNMP secret. The monitoring YAML contains only
deployment safety policy. Atlas resolves and plans the desired state; FortiGate
and Zabbix hold the reconciled runtime state.

## Deployment

Fresh OpenBao bootstrap creates a separate
`atlas-monitoring-provisioner` AppRole. On an existing installation run:

```console
sudo atlasctl openbao
# Select: 2. Reconcile Atlas backend roles
```

The role creates, reads, or updates only `atlas/devices/snmp/*`. It cannot
read operator-managed device credentials, delete secrets, or access deployment
secrets. FortiGate API tokens are resolved through the same `atlas-backend`
credential service used by Profylaxia. The canonical KV v2 path for generated communities is
`atlas/devices/snmp/fortinet/<netbox-device-id>/monitoring`.

Backend deployment creates `.runtime/monitoring/config.yaml` from a safe
default, `.runtime/monitoring/operator-token`, and a persistent SQLite job
directory. The default permits only TEST-NET-1 and has no monitoring source IP.
Build the site policy from `deployment/monitoring.example.yaml`, then redeploy
the backend. Schema v1 is rejected with
`monitoring_configuration_v1_migration_required`; it is never deleted or
silently reinterpreted.

Keep customer CA files under `.runtime/monitoring/trust/` and use their
container path, for example `/run/atlas/monitoring/trust/fortigate-ca.pem`. TLS verification is
required by default. A site policy `tls_verify: false` is accepted only
together with `lab_tls_exception: true`.

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
2. Assign the primary IP Address object to the management `dcim.interface`.
3. Add an exact NetBox `fortios-api` TCP service only when the API does not use
   the default port 443.
4. Store the FortiOS API token in the existing device credential profile.
5. Configure the allowed management networks and one site-level Zabbix egress
   `monitoring.source_ip`. Atlas blocks provisioning if it cannot determine it.
6. In the Atlas UI enter the operator token, select a mode, and inspect. The
   plan shows the API endpoint, interface, resolved VDOM, ports, source IP,
   credential profile, and template without secret values.
7. Review and confirm the plan. Plans expire after 15 minutes and are rejected
   if inspected state changes.
8. Follow the job until `COMPLETED`, `FAILED`, or `PARTIAL`. Verification
   `PENDING` means Zabbix has not returned a fresh successful `sysObjectID`.

Atlas never removes customer SNMP communities. A community conflict, unmanaged
Zabbix host collision, unexpected schema, or unsupported version stops the
operation. An interrupted write job becomes `PARTIAL` and requires a new plan.

Zabbix writes from both `atlasctl zabbix sync` and approved monitoring jobs are
owned by the shared `ZabbixSyncService`. Runtime reconciliation uses the
deployment-managed `Aricoma Atlas` host group and records the NetBox site in the
`atlas_site` ownership tag. The restricted runtime API identity does not create
host groups and does not require the Zabbix Super admin user type. Vendor
drivers only produce device state and SNMP credentials; FortiGate REST and
future Ansible-backed vendors feed the same Zabbix reconciliation boundary.
The sync service prefers an Atlas-generated per-device monitoring secret when
one exists and otherwise retains the existing operator-managed SNMP profile
lookup. Generated secret paths use
`atlas/devices/snmp/<manufacturer-slug>/<netbox-device-id>/monitoring`.

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

P0 accepts FortiOS `7.0.9` and Zabbix `7.0.x` only. The management interface
is resolved from the primary IP assignment, the API port from the shared
`fortios-api` service resolver, and VDOM context from read-only FortiOS state.
Ambiguous assignments or VDOMs fail closed. The FortiGate field
contract was checked against Fortinet's `fortinet.fortios` 2.6.0
`fortios_system_snmp_community` module. Live FortiOS 7.0.9 schema response and
host mask behavior still require a lab integration test before production;
static tests cannot verify an appliance API. The live LAB acceptance flow
(`10.200.200.25:444`) therefore remains a separate required validation and is
not claimed as production-verified.

Primary references:

- FortiOS 7.0.9 CLI reference:
  <https://docs.fortinet.com/document/fortigate/7.0.9/cli-reference/708841/cli-configuration-commands>
- Fortinet Ansible collection 2.6.0:
  <https://github.com/fortinet-ansible-dev/ansible-galaxy-fortios-collection/tree/2.6.0>
- Zabbix 7.0 secret user macros:
  <https://www.zabbix.com/documentation/7.0/en/manual/api/reference/usermacro/object>
- Zabbix 7.0 host interfaces:
  <https://www.zabbix.com/documentation/7.0/en/manual/api/reference/hostinterface/object>
