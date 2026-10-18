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

The role creates, reads, or updates only the terminal SNMP leaf at
`atlas/devices/credentials/<manufacturer-slug>/<device-name>/snmp`. For example,
device `SWtento` owned by manufacturer slug `cisco` uses
`atlas/devices/credentials/cisco/SWtento/snmp`. The OpenBao policy cannot write
the sibling `admin` or `automation` credentials, delete secrets, or access
deployment secrets. FortiGate API tokens and SSH credentials are resolved
through the same `atlas-backend` credential service used by Profylaxia.

Legacy Atlas-generated values at
`atlas/devices/snmp/<manufacturer-slug>/<netbox-device-id>/monitoring` remain
read-only. During an explicitly approved `provision_and_monitor` operation,
Atlas copies a valid legacy value to the canonical named path with KV v2
`cas=0`. It never overwrites an existing canonical value and never deletes the
legacy value. `monitor_only` may read the legacy value but never performs this
migration. After a NetBox device rename, `monitor_only` stops until the new
named path exists. An approved `provision_and_monitor` operation creates a new
community at the new path and reconciles it to the device; the old named secret
remains untouched. To preserve the same community across a rename, move it
explicitly before reconciliation. Atlas does not guess whether two names
identify the same device. Device names used for this path must contain only
letters, digits, `.`, `_`, or `-` and must start with a letter or digit.

`sudo atlasctl configure` creates `.runtime/monitoring/config.yaml` together
with the proxy/TLS site policy from one validated interactive or declarative
input. See [First-boot site configuration](site-configuration.md). If that
command has not been run, backend deployment creates a safe default and a
persistent SQLite job directory. The shared Atlas operator bearer token used by
both monitoring and Profylaxia is stored in OpenBao at `atlas/backend` under
the `operator_token` key. Backend deployment materializes the same value into
`.runtime/monitoring/operator-token` with mode `0600` for a read-only container
mount. On the first upgraded deployment, an existing valid local token is
copied to the missing OpenBao key without rotation. Afterwards `atlas/backend`
is the source of truth. If the local migration source does not exist,
deployment generates a new token and stores it in OpenBao before creating the
runtime file.
The default permits the three RFC1918 private IPv4 ranges but has no monitoring
egress, so provisioning remains blocked until the operator sets one legacy
`monitoring.source_ip` or named `monitoring.egress_profiles`. Profiles select
the source IP and optional existing Zabbix proxy by longest target-prefix
match. A direct Atlas-hosted Zabbix server may use different VM addresses when
Linux routing selects them for different target networks. Direct profiles are
validated against the Atlas host's `ip route get` result during deployment.
Narrow `allowed_device_networks` to the customer management ranges when they
are known. Existing runtime configuration is never
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
5. Configure the allowed management networks and either one compatibility
   `monitoring.source_ip` or explicit egress profiles. Atlas blocks provisioning
   when no profile matches, mappings are ambiguous, or the direct host route
   selects a different source IP.
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
lookup. New generated secret paths use
`atlas/devices/credentials/<manufacturer-slug>/<device-name>/snmp`; the old
numeric path remains a read-only compatibility source as described above.

## Zabbix template selection and metrics

`catalog/zabbix.yaml` is the single reviewed mapping. Each supported platform
has a conservative default template. A device-type rule may replace that
default only when the exact NetBox Device Type slug matches a reviewed pattern.
If different rules match different templates, Atlas stops with an ambiguity
error instead of choosing one silently.

Current Aruba rules are deliberately narrow:

- `hpe-aruba-8325-*` and `hpe-aruba-8360-*` use the official
  `Aruba CX 8300s by SNMP` template;
- `hpe-aruba-2920-*` uses the official `HP Enterprise Switch by SNMP`
  template;
- other AOS-CX and AOS-Switch device types retain
  `Network Generic Device by SNMP` until their OID contract is reviewed.

The inspect/plan response exposes both `resolved.zabbix_template` and the
reviewed `resolved.zabbix_metrics`. The Atlas graph resolver recognizes the
official Aruba CPU and memory keys as well as the standard Zabbix keys. During
fresh deployment and upgrade, Atlas validates that every catalog-referenced
template exists after importing its own templates. A missing official template
stops deployment with its exact name; it is not silently replaced with a
generic template. Zabbix does not automatically update installed templates, so
template upgrades remain an explicit deployment concern.

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

**Plan all** prepares a per-device preview before any provisioning writes.
Review the planned changes and confirm them with the separate apply action.
A planning error blocks the bulk apply; fix the affected device and generate
fresh plans before retrying.

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
- Zabbix 7.0 official network templates repository:
  <https://git.zabbix.com/projects/ZBX/repos/zabbix/browse/templates/net?at=release%2F7.0>
- Zabbix 7.0 template changes and manual upgrade requirement:
  <https://www.zabbix.com/documentation/7.0/en/manual/installation/template_changes>
