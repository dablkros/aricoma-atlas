# FortiOS Profylaxia: API and SSH collection contract

Status: implementation boundary as of 2026-10-24. The command and selector
matrix below is based on Fortinet FortiOS 8.0 documentation and
`fortinet.fortios` collection 2.6.0. Runtime support still requires an
anonymized fixture from the actual FortiOS branch and model; an unverified
shape remains `UNKNOWN` or `UNSUPPORTED`.

## Transport selection

Atlas reads the explicit NetBox platform `fortios`. An exact per-device
Application Service named `fortios-api` with one TCP port selects HTTPS and an
`api_token` from:

```text
devices/credentials/fortinet/<credential_profile>/prophylaxis
```

If that service is absent, Atlas selects SSH/22 and reads `username` and
`password` from the same secret path. An existing API service never silently
falls back to SSH. Both transports map to the same thematic collectors and
stable check IDs.

## Device-native discovery

NetBox selects the Profylaxia areas, but it does not contain a manually curated
list of expected interfaces, routes, HA members, VPN tunnels or SD-WAN members.
Atlas discovers the objects exposed by the selected FortiOS transport and stores
their current values in the structured run result.

This iteration is collection-first. It does not apply customer-specific CPU,
memory, interface-utilization, certificate, VPN or SD-WAN thresholds. Where the
device exposes an unambiguous intrinsic state, Atlas preserves that evidence.
Where operational intent is required, the result remains `UNKNOWN` rather than
manufacturing `PASS`, `WARNING` or `FAIL`.

For SSH, physical interface names are discovered from `get system interface`.
Only names matching the module's restricted interface-name grammar are used to
build `diagnose hardware deviceinfo nic <interface>` commands. Device-provided
text is never passed to a general shell.

## Collector matrix

| Area/checks | HTTPS monitor selectors | SSH read-only evidence | Evaluation boundary |
| --- | --- | --- | --- |
| identity, OS, serial, uptime | `system_status`, `/api/v2/monitor/web-ui/state` | `get system status`, `get system performance status` | Exact parsed values; model/serial reconciliation remains separate. |
| CPU, memory, sessions | `system_resource_usage`, `system_performance_status` | `get system performance status`, `diagnose sys session stat`, `diagnose hardware sysinfo memory`, `diagnose hardware sysinfo conserve` | Values are collected without Atlas warning/critical thresholds. One sample is not a trend. |
| stability, storage, sensors | `system_sensor-info`, `system_storage`, crash/config-error selectors | sensor, disk, PSU, TPM, crash and config-error diagnostics | Unsupported model-specific commands remain unsupported; empty current evidence is not proof of historical stability. |
| interfaces | `system_interface` | `get system interface`, netlink status/rates and dynamically discovered NIC diagnostics | All exposed interfaces are inventoried. A down port is not automatically a fault. Absolute counters remain informational until reliable deltas exist. |
| licenses, FortiGuard | `license_status`, `system_fortiguard_server-info` | system status and autoupdate status/versions | Entitlement validity, database currency and service reachability are reported separately. Old or N/A database values do not fail by themselves. |
| certificates, ACME | `system_available-certificates`, `system_acme-certificate-status` | local/CA certificate details and `diagnose sys acme list-domains` | Expiration dates are collected without warning/critical thresholds. Stored certificates differ from service-presented certificates. ACME configuration alone does not prove renewal. |
| NTP | `system_ntp_status` | `diagnose sys ntp status` | PASS only for an explicit synchronized state. Diagnose permission denial is unknown. |
| routing | `router_ipv4`, `router_ospf_neighbors`, `router_bgp_neighbors` | routing table, BGP networks, OSPF neighbors and BGP summary | Tables and discovered neighbors are reported without a required-route policy. |
| HA | HA statistics, history and checksum selectors | HA status, checksum and history diagnostics | Standalone is N/A. Cluster evidence is collected without a manually declared member list. |
| SD-WAN, IPsec | `virtual-wan_health-check`, `vpn_ipsec` | SD-WAN health and VPN tunnel diagnostics | Discovered states and SLA measurements are reported without customer thresholds or assumed always-up tunnels. |
| recent events, delivery | FortiAnalyzer queue selector; crash/config logs | limited event log plus FortiAnalyzer forwarding setting | Configuration is not receiver-side proof. Delivery remains unknown without queue/receiver evidence. |
| backup | existing Oxidized node/job state | existing Oxidized node/job state | Profylaxia does not trigger a backup. Last successful collection differs from last configuration change and restore testing. |
| firmware/lifecycle/PSIRT | future reviewed Fortinet integration | installed version/model only | Installed data is collected; recommendation, lifecycle and PSIRT stay unverified until an official integrated source is reviewed. |

## Known limitations and required fixtures

- API payload normalization is conservative until real anonymized results are
  captured for every selector on supported FortiOS branches.
- VDOM collection currently executes in `root`; multi-VDOM fan-out is not yet
  implemented.
- Interface counter history and stability trends are not yet indexed across
  run artifacts, so single-sample values cannot prove sustained health.
- Presented-service TLS validation, FortiAnalyzer receiver confirmation,
  recommended firmware, lifecycle, support entitlement and PSIRT correlation
  require separate verified contracts.
- Needed fixtures: physical appliance sensors/disk/PSU; HA pair with checksum
  mismatch and failover history; BGP/OSPF; SD-WAN SLA; always-up and on-demand
  IPsec; ACME success/failure; expired and valid subscriptions; API permission
  denial; multi-VDOM results; and FortiAnalyzer queue states.

Primary references:

- [FortiOS 8.0 CLI troubleshooting cheat sheet](https://docs.fortinet.com/document/fortigate/8.0.0/cli-troubleshooting-cheat-sheet/420966/cli-troubleshooting-cheat-sheet)
- [FortiOS 8.0 `diagnose sys` CLI reference](https://docs.fortinet.com/document/fortigate/8.0.0/cli-reference/235530229)
- [FortiOS 8.0 VPN tunnel CLI reference](https://docs.fortinet.com/document/fortigate/8.0.0/cli-reference/537874326/diagnose-vpn-tunnel)
- [`fortinet.fortios` 2.6.0 monitor facts](https://docs.ansible.com/projects/ansible/latest/collections/fortinet/fortios/fortios_monitor_fact_module.html)

