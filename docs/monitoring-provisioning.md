# Monitoring provisioning

Atlas reconciles explicitly supported NetBox platforms into SNMPv2c monitoring in
Zabbix. `monitor_only` reads an existing device-specific SNMP secret and changes
only the Atlas-owned Zabbix host. `provision_and_monitor` also reconciles the
device's Atlas-owned read-only community. Supported platform contracts are
FortiOS, Cisco IOS/IOS-XE, Cisco CBS/CBS250-350, Aruba AOS-CX, and Aruba
AOS-Switch. There is no manufacturer-wide fallback.

No write endpoint accepts a management address, API token, SNMP community,
template, or proxy from the caller. Those values come from NetBox, OpenBao,
`catalog/zabbix.yaml`, and the root-owned site policy.

NetBox owns device identity, primary IPv4, platform, monitoring selection, and
`credential_profile`. OpenBao owns operator-managed API credentials and the
Atlas-generated device SNMP secret. The monitoring YAML contains only
deployment safety policy. Atlas resolves and plans the desired state; Ansible
executes device mutations; the network device and Zabbix hold the reconciled
runtime state.

## Deployment

Fresh OpenBao bootstrap creates a separate
`atlas-monitoring-provisioner` AppRole. On an existing installation run:

```console
sudo atlasctl openbao
# Select: 2. Reconcile Atlas backend roles
```

The role creates, reads, or updates only `atlas/devices/snmp/*`. It cannot
read operator-managed device credentials, delete secrets, or access deployment
secrets. FortiGate API tokens and SSH credentials are resolved through the same `atlas-backend`
credential service used by Profylaxia. The canonical KV v2 path for generated communities is
`atlas/devices/snmp/<manufacturer-slug>/<netbox-device-id>/monitoring`.

`sudo atlasctl configure` creates `.runtime/monitoring/config.yaml` together
with the proxy/TLS site policy from one validated interactive or declarative
input. See [First-boot site configuration](site-configuration.md). If that
command has not been run, backend deployment creates a safe default,
`.runtime/monitoring/operator-token`, and a persistent SQLite job directory.
The default permits the three RFC1918 private IPv4 ranges but has no monitoring
source IP, so provisioning remains blocked until the operator sets
`monitoring.source_ip`. Narrow `allowed_device_networks` to the customer
management ranges when they are known. Existing runtime configuration is never
overwritten; update it explicitly and redeploy the backend. Schema v1 is rejected with
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

1. In NetBox set `monitoring_enabled=true`, active status, an explicitly
   supported platform, primary management IPv4, site, and `credential_profile`.
2. For FortiOS, assign the primary IP Address object to the management
   `dcim.interface`.
3. For FortiOS, add an exact NetBox `fortios-api` TCP service only when the API does not use
   the default port 443.
4. Store the FortiOS API token or SSH username/password in the existing device
   credential profile.
5. Configure the allowed management networks and one site-level Zabbix egress
   `monitoring.source_ip`. Atlas blocks provisioning if it cannot determine it.
6. In the Atlas UI enter the operator token, select a mode, and inspect. The
   plan shows the resolved API or SSH endpoint, applicable FortiOS context,
   ports, source IP, credential profile, and template without secret values.
7. Review and confirm the plan. Plans expire after 15 minutes and are rejected
   if inspected state changes.
8. Follow the job until `COMPLETED`, `FAILED`, or `PARTIAL`. Verification
   `PENDING` means Zabbix has not returned a fresh successful `sysObjectID`.

Atlas never removes customer SNMP communities. It modifies only the
`ATLAS-SNMP-V2C` ACL on platforms that support a source ACL. A community conflict, unmanaged
Zabbix host collision, unexpected schema, or unsupported version stops the
operation. An interrupted write job becomes `PARTIAL` and requires a new plan.

Cisco IOS/IOS-XE and Aruba AOS-CX bind the generated community to an
Atlas-owned source ACL. Cisco CBS uses the vendor's source-IP parameter. The
`community.ciscosmb` collection is command-only, so Atlas performs a read
before the guarded configuration command and verifies the state afterwards.
Aruba AOS-Switch 16.x can create a read-only `operator restricted` community,
but its documented community command cannot bind it to a monitoring source.
Provisioning that platform therefore requires the explicit site policy
`monitoring.allow_unrestricted_snmp_v2c: true`; keep it false unless a
management VLAN ACL or firewall provides equivalent source restriction.

Zabbix writes from both `atlasctl zabbix sync` and approved monitoring jobs are
owned by the shared `ZabbixSyncService`. Runtime reconciliation uses the
deployment-managed `Aricoma Atlas` host group and records the NetBox site in the
`atlas_site` ownership tag. The restricted runtime API identity does not create
host groups and does not require the Zabbix Super admin user type. Vendor
drivers only produce device state and SNMP credentials; every device mutation
is executed by the isolated `configure_monitoring.yml` Ansible playbook and
feeds the same Zabbix reconciliation boundary.
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

The Operations UI shows the active job stage and posts an immediate notification
when inspection or reconciliation starts. A completed Zabbix configuration with
verification `PENDING` is displayed as waiting for the first SNMP sample, not as
a fully verified success. The browser continues polling for up to two minutes;
after that the operator can use **Check verification** without repeating device
or Zabbix configuration.

## Validated contracts and limits

The FortiOS path accepts FortiOS `7.0.9`; the Zabbix boundary accepts `7.0.x`.
The FortiGate management interface
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
- Cisco IOS SNMP resource module, cisco.ios 11.5.1:
  <https://docs.ansible.com/projects/ansible/latest/collections/cisco/ios/ios_snmp_server_module.html>
- Cisco CBS250/350 SNMP CLI reference (updated 2025-02-28):
  <https://www.cisco.com/c/en/us/td/docs/switches/lan/csbms/CBS_250_350/CLI/cbs-250-cli/snmp-commands.html>
- Aruba AOS-CX 10.13 SNMP/MIB guide:
  <https://www.arubanetworks.com/techdocs/AOS-CX/10.13/PDF/snmp_mib.pdf>
- Aruba AOS-Switch 16.09 management guide:
  <https://www.arubanetworks.com/techdocs/AOS-Switch/16.09/Aruba%202530%20Management%20and%20Configuration%20Guide%20for%20ArubaOS-Switch%2016.09.pdf>
- Zabbix 7.0 secret user macros:
  <https://www.zabbix.com/documentation/7.0/en/manual/api/reference/usermacro/object>
- Zabbix 7.0 host interfaces:
  <https://www.zabbix.com/documentation/7.0/en/manual/api/reference/hostinterface/object>
