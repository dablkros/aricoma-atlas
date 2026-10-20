# Profylaxia system metrics: Cisco CBS350 and Aruba AOS-CX

Status: implementation contract and evidence register, reviewed against the
repository on 2026-10-18.

## Boundary and data flow

Atlas keeps the existing ownership boundaries:

1. NetBox selects Profylaxia groups through `profylaxia_checks` and supplies
   device identity, platform, model and management IP. Atlas expands a group to
   its stable internal check IDs before platform planning.
2. OpenBao supplies the device-specific SNMP credential from
   `devices/credentials/<vendor>/<device-name>/snmp`. If that Atlas-managed leaf
   does not exist, the explicitly referenced credential profile is used. No
   credential is written to a result file or the browser.
3. The existing Profylaxia run service groups expanded system checks into one
   `snmp_system` collector call per device.
4. The existing Ansible Runner boundary invokes a local, read-only SNMPv2c GET
   module. Only OIDs declared in the platform task are queried; there is no
   unrestricted `.1` walk.
5. Normalized, secret-free results are atomically stored in
   `/run/atlas/prophylaxis/runs/<run-id>/result.json` and `report.txt`.
6. Operations UI starts the existing `POST /api/prophylaxis/runs` operation,
   polls it and displays each device/check evaluation.

Zabbix is not called by this collector. Zabbix remains responsible for periodic
sampling, trends and alert history; Profylaxia executes only checks selected in
NetBox.

## Resources group, stable check IDs and outcomes

The NetBox value `resources` expands to the system checks below. Legacy stored
individual values remain accepted during migration.

| NetBox value | Meaning | CBS350 | Aruba CX |
| --- | --- | --- | --- |
| `cpu_utilization` | CPU 1 s, 1 min and 5 min | Three verified CBS OIDs | `UNSUPPORTED` |
| `memory_utilization` | RAM utilization | `UNSUPPORTED`; no RAM OID verified | `UNSUPPORTED` |
| `cpu_temperature` | Tested temperature reading | Raw number, `UNKNOWN`; unit and threshold unverified | `UNSUPPORTED` |
| `fan_health` | Model-aware fan state | Three tested status OIDs | `UNSUPPORTED` |

Legacy stored values `CPU utilization`, `Memory utilization` and `Temperature`
remain accepted during migration. Fresh NetBox installs offer the `resources`
group instead of individual metric choices.

- `PASS`: a validated healthy result.
- `FAIL`: a confirmed fault under the documented state model and explicit
  hardware expectation.
- `NOT_APPLICABLE`: the component is not part of the documented hardware model.
- `UNKNOWN`: data exists, but verified meaning is insufficient for a decision.
- `UNSUPPORTED`: no verified metric contract exists for this context.

`UNKNOWN` and `FAIL` make a run partial. `NOT_APPLICABLE` and `UNSUPPORTED` are
counted as skipped, while their structured values and reason remain in JSON and UI.

## Evidence register

### Cisco CBS350

| Data | OID | Evidence | Atlas interpretation |
| --- | --- | --- | --- |
| CPU 1 second | `1.3.6.1.4.1.9.6.1.101.1.7.0` | Real device GET + Cisco document | percent, 0-100 |
| CPU 1 minute | `1.3.6.1.4.1.9.6.1.101.1.8.0` | Real device GET + Cisco document | percent, 0-100 |
| CPU 5 minutes | `1.3.6.1.4.1.9.6.1.101.1.9.0` | Real device GET + Cisco document | percent, 0-100 |
| Temperature | `1.3.6.1.4.1.9.6.1.101.53.15.1.10.1` | Real device GET; reviewed Cisco text does not establish a unit | raw value only; no Celsius label or alert threshold |
| Fan 1..3 | suffixes `.4.1`, `.5.1`, `.6.1` under `1.3.6.1.4.1.9.6.1.101.53.15.1` | Real device GET + Cisco enumeration | 1 normal; 2 warning; 3 critical; 4 shutdown; 5 notPresent; 6 notFunctioning |
| RAM | none | Community templates expose IOS `CISCO-MEMORY-POOL-MIB`, not a verified CBS350 object | `UNSUPPORTED` |

The Cisco Business 350 data sheet identifies `CBS350-16T-2G` as fanless. For that
exact model, status `5/notPresent` is `NOT_APPLICABLE`. Atlas does not extend this
assumption to unknown models: there it remains `UNKNOWN`. A future explicitly
declared fan-equipped model must treat a missing expected fan as `FAIL`.

Primary sources:

- [Cisco, Common SNMP OIDs for CBS250/CBS350](https://www.cisco.com/c/en/us/support/docs/smb/switches/Cisco-Business-Switching/kmgmt3636-snmpv3-common-oids-cbs350.pdf), reviewed 2026-10-18.
- [Cisco Business 350 Series Managed Switches Data Sheet](https://www.cisco.com/c/en/us/products/collateral/switches/business-350-series-managed-switches/datasheet-c78-744156.html), 2024 edition, reviewed 2026-10-18.

### Aruba AOS-CX OVA

Real tests on `ABC123 ArubaOS-CX_OVA`, `Virtual.10.07.0004`, confirmed standard
system identity and uptime. The two supplied enterprise OIDs returned
`No Such Object`; a whole-tree `.1` walk timed out. Atlas probes only the explicit
objects and never interprets absence as zero utilization. CPU, RAM, temperature and
fan checks therefore return `UNSUPPORTED` in the current Aruba CX contract. This
does not claim that every physical Aruba CX model lacks these metrics; each physical
model/firmware needs a separately verified OID or API contract.

## Zabbix template comparison

No community template is imported automatically.

| Reference | Finding | Decision |
| --- | --- | --- |
| Community `Cisco_SG300-52-d`, export 6.0 | Same CBS CPU family; own interface discovery | OIDs corroborate the test; do not duplicate interface discovery |
| Community `SNMP Cisco SG500x`, export 6.0 | CBS 5-minute CPU OID and Radlan environment tables | Reference only; model generation/indexes differ |
| Community `Cisco_SB_SG350XG-2F10` and `SG500X_48P`, export 6.0 | Model/index-specific sensors and ports | Not a generic CBS350 contract |
| Community `SNMP Cisco Memory`, export 6.0 | `CISCO-MEMORY-POOL-MIB` under `.1.3.6.1.4.1.9.9.48` | Does not prove CBS350 RAM support |
| Official `Cisco IOS by SNMP`, Zabbix 7.0, vendor version `7.0-4` | IOS 12.2(3.5)+; IOS-specific PROCESS, MEMORY and ENVMON MIBs | Keep for IOS; do not attach automatically to CBS350 |

The reviewed community exports declare Zabbix format `6.0`; Atlas deploys Zabbix
7.0.31. Import compatibility and runtime behavior were not validated, so they stay
research-only. The Atlas CBS template continues to own periodic CBS metrics and
existing interface discovery remains unchanged.

References:

- [Zabbix Community Templates – Cisco](https://github.com/zabbix/community-templates/tree/main/Network_Devices/Cisco), reviewed 2026-10-18.
- [Zabbix forum discussion: CBS350, SG500 and SG350](https://www.zabbix.com/forum/zabbix-help/508482-monitoring-cisco-switches-cbs350-sg500-and-sg350), community evidence only, reviewed 2026-10-18.
- [Official Zabbix 7.0 Cisco IOS by SNMP template](https://github.com/zabbix/zabbix/blob/release/7.0/templates/net/cisco/cisco_snmp/template_net_cisco_snmp.yaml), vendor version `7.0-4`, reviewed 2026-10-18.

## Operations

1. Enable Profylaxia on the NetBox device and select the `resources` group.
2. Ensure the Atlas-managed per-device SNMP secret exists (normally created by
   `Provision & monitor`) or configure the explicit SNMP credential profile.
3. In Operations UI, enter the operator token and select **Run Profylaxia**.
4. Read the per-check outcome in UI and the run files on the Atlas VM. A high
   observed value is a sample, not a hard-coded state.

Current limitation: this collector supports SNMPv2c. An SNMPv3 credential is not
silently downgraded. Live validation against the named CBS350 and Aruba OVA remains
required after deployment; repository tests validate normalization and planning,
not device reachability.
