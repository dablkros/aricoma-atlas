# Changelog

All notable changes to Aricoma Atlas are documented here. A release section is
final only after its commit passes the complete release workflow and receives
the corresponding Git tag.

## [0.4.3] - 2026-10-26

### Added

- NetBox inventory import from CSV/XLSX and pasted spreadsheet data with a
  shared dry-run plan, per-row results, explicit confirmation and guarded apply.
- Operations UI import workflow with visible phase/elapsed-time feedback,
  runtime Device Type YAML upload and automatic plan retry.
- Device Type preflight across NetBox, the built Atlas catalog, the pinned
  upstream Device Type Library and an operator-managed persistent runtime
  catalog.
- Standard NetBox Device Roles for network devices, switches, routers,
  firewalls, load balancers and management appliances, including deterministic
  model/platform mapping and a conservative `Network Device` fallback.
- Tenant and default Site provisioning through first-boot configuration and
  `atlasctl inventory` administration commands.
- Cisco Software Suggestion API V2 and EoX integration backed by OpenBao for
  normalized firmware recommendation and lifecycle results.
- Extended FortiOS prophylaxis collection and evaluation through both REST API
  and SSH, selected by the explicit NetBox `fortios-api` service.

### Changed

- Refreshed the Operations UI visual design and grouped inventory/prophylaxis
  actions without exposing shell execution or the Docker socket.
- Device Type matching accepts an exact manufacturer-and-model input while
  continuing to reject ambiguous definitions.
- Prophylaxis run artifacts provide compact per-device structured data alongside
  the existing human-readable report.
- Align package, deployment image, README and example environment versions to
  0.4.3.

### Security

- Inventory apply is authorized with the operator token, bound to the reviewed
  input by a SHA-256 plan fingerprint and revalidated immediately before writes.
- Uploaded Device Type YAML is schema-validated and stored in the protected
  runtime catalog; arbitrary URLs are not fetched by Atlas.

### Known limitations

- Inventory import does not create unknown customer-specific Sites, Platforms
  or VRFs and does not repair conflicting existing Devices or IP addresses.
- Role selection never infers device purpose from hostname; ambiguous models
  require an explicit row or configured mapping.
- Vendor API availability and entitlement can still produce explicit
  `UNKNOWN` results rather than a firmware or lifecycle verdict.

## [0.4.2] - 2026-10-22

### Added

- First-install selection of Oxidized and Zabbix while NetBox, OpenBao,
  Ansible and prophylaxis remain the fixed Atlas core. The protected component
  manifest is preserved by updates and supports add-only expansion.
- Categorized Cisco IOS and Cisco CBS prophylaxis collectors for resources,
  interfaces, operations and compliance, including legacy Cisco SSH algorithms
  and an explicit NetBox service-driven Telnet path.
- Check Point Gaia device-facts collection through a dedicated clish module and
  explicit Gaia platform/catalog mappings.
- Serial-number and model inventory checks with guarded NetBox reconciliation,
  plus normalized report formatting and optional Zabbix chart artifacts.
- Cisco CBS350-12XT catalog data and expanded platform/device mappings.

### Changed

- Deploy, ingress, backend networks, readiness, Operations UI and `atlasctl`
  now follow the persisted optional-component selection.
- Prophylaxis results retain normalized values, evidence and evaluation status
  instead of reducing successful collection to a generic `collected` message.
- Existing installations without a component manifest retain the legacy full
  Oxidized and Zabbix stack during upgrade.
- Align package, deployment image, README and example environment versions to
  0.4.2.

### Known limitations

- Optional-component removal is deliberately unsupported; deployed components
  may only be retained or extended.
- Cisco licensing, recommended-software and product end-of-life lookups are not
  part of this release.
- New first-install component combinations require GitHub Actions and VM
  acceptance after the release commit is pushed; no local test suite was run
  during release preparation.

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
