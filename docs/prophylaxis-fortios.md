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

## Explicit evaluation policy

The optional NetBox JSON custom field `profylaxia_expectations` stores only
expected state and Atlas policy. Atlas validates it before opening a device
connection. Missing expectations do not manufacture a PASS or FAIL.

```json
{
  "thresholds": {
    "cpu_warning_percent": 75,
    "cpu_critical_percent": 90,
    "memory_warning_percent": 75,
    "memory_critical_percent": 90,
    "certificate_warning_days": 60,
    "certificate_critical_days": 30
  },
  "interfaces": {"required_up": ["port1", "port2"]},
  "routing": {
    "required_routes": ["0.0.0.0/0"],
    "bgp_neighbors": ["192.0.2.1"],
    "ospf_neighbors": []
  },
  "ha": {
    "mode": "active-passive",
    "members": ["FGT-A", "FGT-B"],
    "monitored_interfaces": ["port1"]
  },
  "vpn": {"required_up": ["hq"], "on_demand": ["support"]},
  "sdwan": {"members": ["wan1", "wan2"], "health_checks": ["internet"]},
  "vdoms": ["root"]
}
```

Thresholds are Atlas operational policy, not Fortinet vendor limits. The JSON
result records transport, VDOM, collection time, rule, evidence and recommended
action without storing raw credentials or a full configuration.

## Collector matrix

| Area/checks | HTTPS monitor selectors | SSH read-only evidence | Evaluation boundary |
| --- | --- | --- | --- |
| identity, OS, serial, uptime | `system_status`, `/api/v2/monitor/web-ui/state` | `get system status`, `get system performance status` | Exact parsed values; model/serial reconciliation remains separate. |
| CPU, memory, sessions | `system_resource_usage`, `system_performance_status` | `get system performance status`, `diagnose sys session stat`, `diagnose hardware sysinfo conserve` | CPU/memory need configured policy thresholds. One sample is not a trend. Conserve mode is an explicit failure. |
| stability, storage, sensors | `system_sensor-info`, `system_crash-log_download`, `system_config-error-log_download` | sensor, disk, crash and config-error diagnostics | Explicit faults can fail; empty current evidence without history remains unknown. VM-only hardware checks can be not applicable. |
| interfaces | `system_interface` | physical interface and netlink status/rates | A down port fails only when it is in `interfaces.required_up`. The traffic-history selector still needs a verified parameter contract; absolute counters remain unknown until a reliable delta exists. |
| licenses, FortiGuard | `license_status`, `system_fortiguard_server-info` | system status and autoupdate status/versions | Entitlement validity, database currency and service reachability are reported separately. Old or N/A database values do not fail by themselves. |
| certificates, ACME | `system_available-certificates`, `system_acme-certificate-status` | local/CA certificate details and `diagnose sys acme list-domains` | Stored certificates and ACME status are distinct from a certificate presented by a service. SSH lists domains but does not call `status-full <domain>` until a verified per-domain plan exists. ACME configuration alone does not prove renewal. |
| NTP | `system_ntp_status` | `diagnose sys ntp status` | PASS only for an explicit synchronized state. Diagnose permission denial is unknown. |
| routing | `router_ipv4`, `router_ospf_neighbors`, `router_bgp_neighbors` | routing table, OSPF neighbors, BGP summary | Required routes/neighbors come from NetBox. Without expectations, collected state remains informational/unknown. |
| HA | HA statistics, history and checksum selectors | HA status, checksum and history diagnostics | Standalone is N/A unless HA was expected. Expected members must be present; primary reachability alone is insufficient. |
| SD-WAN, IPsec | `virtual-wan_health-check`, `vpn_ipsec` | SD-WAN health and VPN tunnel diagnostics | Required health checks and always-up tunnels come from NetBox. On-demand tunnels are not failed merely for being idle. |
| recent events, delivery | FortiAnalyzer queue selector; crash/config logs | limited event log plus FortiAnalyzer forwarding setting | Configuration is not receiver-side proof. Delivery remains unknown without queue/receiver evidence. |
| backup | existing Oxidized node/job state | existing Oxidized node/job state | Profylaxia does not trigger a backup. Last successful collection differs from last configuration change and restore testing. |
| firmware/lifecycle/PSIRT | future reviewed Fortinet integration | installed version/model only | Installed data is collected; recommendation, lifecycle and PSIRT stay unverified until an official integrated source is reviewed. |

## Known limitations and required fixtures

- API payload normalization is conservative until real anonymized results are
  captured for every selector on supported FortiOS branches.
- VDOM collection currently executes in `root`; multi-VDOM fan-out is not yet
  implemented. A non-root expectation therefore must not be reported as
  covered.
- Interface counter history and stability trends are not yet indexed across
  run artifacts, so single-sample values cannot prove sustained health.
- TPM, presented-service TLS validation, FortiAnalyzer receiver confirmation,
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

