# Changelog

All notable changes to Aricoma Atlas are documented here. A release section is
final only after its commit passes the complete release workflow and receives
the corresponding Git tag.

## [0.4.1-beta] - 2026-10-18

### Added

- Ansible monitoring provisioning for Cisco IOS/IOS-XE, Cisco CBS and Aruba
  AOS-CX/AOS-Switch alongside FortiOS, with inspected plans and explicit confirmation.
- Operations UI bulk plan preview, job progress notifications and a separate
  wait/recheck flow for the first successful Zabbix SNMP sample.
- Fresh-VM site configurator, monitoring egress profiles and route-aware source
  selection for multi-address hosts; update.sh for installed deployments.
- Ansible prophylaxis runs, device facts and normalized system-check results,
  including CBS350 CPU/fan checks and explicit unsupported Aruba CX OVA metrics.

### Changed

- Store device SNMP credentials under devices/credentials/<vendor>/<device-name>/snmp
  with guarded legacy migration; store the shared operator token at atlas/backend.
- Refine model-specific Zabbix template selection and Cisco/Aruba configuration
  verification; preserve the separate generic CBS and CBS250/350 platform mappings.
- Align package, deployment image and example environment versions to 0.4.1-beta.

### Known limitations

- Successful provisioning does not imply a verified SNMP sample: PENDING remains
  visible until fresh Zabbix data is available.
- CBS350 RAM and tested Aruba CX OVA system metrics have no verified SNMP contract.
- Physical-device acceptance and production hardening remain separate from CI;
  see docs/monitoring-provisioning.md and docs/prophylaxis-system-metrics.md.

## [0.4.0-beta] - 2026-10-16

### Added

- Zabbix 7.0 monitoring backend, API integration, host reconciliation and
  deployment-managed templates for Cisco CBS250/350 and Sophos SFOS.
- Monitoring configuration schema v2 and guarded FortiGate SNMP provisioning.
- HPE Aruba AOS-CX and AOS-Switch device catalog/platform mappings.
- Experimental Oxidized `ciscosmb` mapping for Cisco CBS220/250/350.
- OpenBao monitoring provisioner AppRole and narrow device-SNMP policy.

### Changed

- Aligned the installer contract with pinned dependencies: Python 3.12+ and
  Ubuntu 24.04/26.04; Ubuntu 22.04 is no longer advertised as supported.
- Replaced the legacy `checkmk_enabled` field with vendor-neutral
  `monitoring_enabled`, including an idempotent migration.
- Extended Atlas backend, Operations UI, `atlasctl`, deployment healthchecks
  and integration coverage for Zabbix and monitoring workflows.
- Expanded the pinned device catalog to 820 types across Cisco, Fortinet,
  Juniper, Sophos and HPE.

### Removed

- Deprecated `checkmk-runtime` policy and active CheckMK runtime dependency.

### Security

- Runtime credentials remain in OpenBao and ephemeral/root-owned runtime
  files; they are not stored in NetBox or tracked Git files.
- Monitoring write operations require a local bearer token and reviewed plan.

### Known limitations

- Oxidized SSH host-key verification remains deliberately disabled with
  `secure: false`; fingerprint commands are diagnostic and do not enforce trust.
- Cisco CBS `ciscosmb` compatibility has not been accepted against physical
  CBS220/250/350 hardware.
- FortiGate monitoring provisioning still requires the documented live-lab
  acceptance test before production use.
- OpenBao server TLS and production SSO/RBAC remain pre-production work.

## [0.3.0-beta] - 2026-10-08

- Released baseline preceding the monitoring, Sophos, Cisco CBS, FortiGate
  provisioning and HPE Aruba work described above.
