# Atlas device platform and credential contract

Status: implemented data model, Oxidized reconciliation and Zabbix monitoring
integration; reviewed 2026-10-16. NetBox 4.7.1 (NetBox Docker 5.1.1),
Oxidized 0.37.0 and Zabbix 7.0.31 LTS.

## Ownership and deployment

| Component | Source of truth / responsibility |
|---|---|
| NetBox | Device identity, manufacturer, Device Type, platform, primary IP, enable flags, explicit `credential_profile` |
| OpenBao | Usernames, passwords, enable passwords, SNMP secrets, KV v2 mount `atlas` |
| Atlas | Declarative catalog enrichment, service mappings, credential resolution and reconciliation orchestration |
| Oxidized | Configuration collection and its independent Git history |
| Zabbix | Current monitoring state, problems and metric history for Atlas-managed hosts |

`sudo atlasctl deploy` retains the existing `bootstrap_netbox.py` flow:

1. Build the pinned community catalog through `build_catalog.py`.
2. Enrich generated Device Type YAML with a platform slug or null using `catalog/platforms.yaml`.
3. Provision existing baseline custom fields, including the optional text field `credential_profile`.
4. Provision manufacturers, then platforms, Device Types and their components.
5. Resolve platform slugs to native NetBox IDs. Set `DeviceType.default_platform` on creation; patch only this field on existing types when different.
6. Perform the existing final dry-run. Repeated deployment adds no duplicate objects or unchanged-field writes.

The mapping SHA-256 is stored in `build/catalog-report.json`. Changing the mapping invalidates a cached catalog and triggers the existing rebuild flow. Direct provisioning also evaluates the current rules, so it cannot apply a stale serialized platform hint.

**Fact (high confidence, pinned source):** NetBox 4.7.1 inherits `DeviceType.default_platform` when saving a **new** Device with no explicit platform. It preserves a supplied platform and does not retroactively update existing Devices. Atlas never writes `/api/dcim/devices/` during bootstrap. [NetBox 4.7.1 Device.save](https://github.com/netbox-community/netbox/blob/v4.7.1/netbox/dcim/models/devices.py).

Unknown types receive a null default. If a rule is removed, the next provisioning clears the catalog-managed Device Type default; it never clears any Device platform. Existing platform names/customizations are preserved. A conflicting manufacturer on an existing platform slug fails explicitly. Single-type imports provision platforms only for the selected manufacturer. The existing baseline provisioner reports custom-field drift instead of silently overwriting operator customization.

## Platform rules and audit

Rules explicitly match `model` or `slug`, scoped by the catalog manufacturer, **only at build/provision time**. Matching is case-sensitive. Conflicting matches to different platforms are fatal. There is no manufacturer-wide fallback.

The reviewed catalog uses names such as `Catalyst 9500-48Y4C`, `FortiGate 60F` and the exceptional `CATALYST 3850 48PT 12 MGIG+36 GIG UPOE`; filename-only examples such as `C9500*` are insufficient. Slug patterns cover those verified aliases.

**Fact (reproducible catalog audit):** pinned upstream `517549215455824be5e3b89965353fd2210cc20c` selects 238 Cisco, 162 Fortinet, 290 Juniper, 21 Sophos and 109 HPE Aruba switch types: 820 total, **625 mapped / 195 unmapped**. The HPE selection is intentionally limited to 68 AOS-CX and 41 AOS-Switch definitions; it does not import HPE servers, storage, AP/IAP, mobility controllers, Instant On, EdgeConnect or Comware devices. The build report contains every unmapped model.

| Platform name | Slug | Mapped Device Types | Oxidized model |
|---|---|---:|---|
| Cisco IOS | `cisco-ios` | 20 | `ios` |
| Cisco IOS XE | `cisco-ios-xe` | 155 | `ios` |
| Cisco NX-OS | `cisco-nxos` | 0 | `nxos` |
| Cisco CBS220 / legacy CBS | `cisco-cbs` | 2 | `ciscosmb` (experimental) |
| Cisco CBS250/350 | `cisco-cbs250-350` | 19 | `ciscosmb` (experimental) |
| FortiOS | `fortios` | 91 | `fortigate` |
| FortiAP | `fortiap` | 21 | unsupported/unmapped |
| FortiAnalyzer | `fortianalyzer` | 3 | unsupported/unmapped |
| Juniper Junos | `junos` | 184 | `junos` |
| Sophos Firewall OS | `sophos-sfos` | 16 | unsupported/unmapped |
| Sophos Access Point | `sophos-ap` | 3 | unsupported/unmapped |
| Sophos SD-RED | `sophos-red` | 2 | unsupported/unmapped |
| Aruba AOS-CX | `aruba-aos-cx` | 68 | `aoscx` |
| Aruba AOS-Switch | `aruba-aos-switch` | 41 | `procurve` |

Fourteen platform objects are prepared by a complete bootstrap. Actual create/existing counts depend on the target NetBox and are printed by its dry-run. NX-OS is available for an explicitly configured Device, but no Nexus is selected by the current manifest, so there is no fabricated Nexus catalog test or broad N9K rule. An N9K name alone also does not settle NX-OS versus ACI.

All pinned Sophos Device Types are cataloged. XG/XGS are assigned
`sophos-sfos`, AP6/APX are assigned `sophos-ap`, and SD-RED is assigned
`sophos-red`. Zabbix uses an Atlas-managed Sophos SFOS template linked to the
official generic SNMP template for firewalls; access points use the generic
template directly. SD-RED remains inventory-only because the reviewed
Sophos documentation describes it as centrally configured through SG UTM or
Sophos Firewall; no direct SD-RED SNMP agent was established. None of the
Sophos platforms receives an unverified Oxidized mapping.

The HPE directory in the upstream library uses `manufacturer: HPE`, including
Aruba-branded models, so Atlas preserves that manufacturer and uses `hpe` in
credential paths. AOS-CX and AOS-Switch remain separate NetBox platforms and
map to the version-pinned Oxidized `aoscx` and `procurve` models respectively.
Both use `Network Generic Device by SNMP` in the first Zabbix integration; the
official Zabbix 7.0 Aruba template is specific to CX 8300s and is not applied
to every Aruba switch family. Physical-device acceptance remains required
before enabling collection in a customer environment.

Unmapped types include Cisco RV/SF/SG/SX; Fortinet FortiSwitch, FortiExtender, FortiAuthenticator, FortiWeb, modules, power supplies and trays; Juniper AP, ACX, PTX, NFX, NetScreen, SRX and newer QFX. These await reviewed rules; “unmapped” does not mean the hardware is unsupported by every product. Newer QFX families are not blindly assigned classic Junos because some run Junos OS Evolved. This conservative boundary can be extended in the same YAML.

**Fact (high confidence, version-specific primary sources):** Oxidized 0.37.0 separates `fortigate` for FortiGate from `fortios` for other Fortinet appliances. Therefore the requested conceptual `fortios → fortios` example is intentionally corrected to **`fortios → fortigate`**. [Fortinet model notes, 0.37.0](https://github.com/ytti/oxidized/blob/0.37.0/docs/Model-Notes/Fortinet.md).

**Experimental / unverified on physical CBS hardware:** both Cisco CBS platforms map explicitly to the pinned `ciscosmb` model. The model's source comment lists older Small Business series, not CBS220/250/350, so the mapping is not evidence of a successful backup on those families. It does not use an `ios` fallback. Hardware FortiAnalyzer and FortiAP compatibility has not been established; those platforms remain unmapped with an explicit validation error.

Audit / dry-run commands (from the repository's Python environment):

```bash
python3 scripts/build_catalog.py
python3 scripts/audit_catalog.py
python3 scripts/bootstrap_netbox.py
python3 scripts/provision_netbox.py --all
```

The builder requires the manifest-pinned `devicetype-library` checkout; the bootstrap prepares it automatically. The last two commands require the existing `NETBOX_URL` / `NETBOX_TOKEN` environment and make no NetBox writes without `--apply`. Audit output includes all unmapped models and platform counts; provisioning prints platform create/existing and Device Type update counts. No placeholder credential secrets are seeded by deployment.

## Credential storage

Only `Device.custom_fields.credential_profile` is stored in NetBox. It is optional globally because not every Device uses a collector, but mandatory for an Oxidized-enabled Device. No username/password or SNMP secret fields are added to NetBox.

KV v2 mount: `atlas`. Relative secret path:

```text
devices/credentials/<manufacturer.slug>/<credential_profile>/<purpose>
```

Examples:

```text
devices/credentials/cisco/cisco-default/admin
devices/credentials/cisco/cisco-default/snmp
devices/credentials/fortinet/fortinet-default/admin
devices/credentials/sophos/sophos-default/snmp
devices/credentials/cisco/SW-CORE-01/admin
```

The last example requires an explicit `credential_profile = SW-CORE-01`. Renaming a Device never changes its reference. Vendor comes from `device.device_type.manufacturer.slug` in the native NetBox REST representation, not a display name. The resolver rejects noncanonical vendor slugs, whitespace, slashes, URL escapes and traversal characters instead of silently rewriting a secret identity. Profile case is preserved; valid profiles match `^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$`.

The HTTP KV v2 data endpoint is `/v1/atlas/data/devices/credentials/...`; the `data` segment is not part of the relative secret path passed to `OpenBaoClient.kv_read`. `device-credentials-read` grants reads beneath `atlas/data/devices/credentials/*`. Existing service AppRoles retain the same policy assignment. `atlas/oxidized/*` continues to hold application secrets such as web login; it is not a per-device `oxidized` purpose.

Required admin shape (placeholders only):

```json
{"username": "<device-account>", "password": "<device-password>"}
```

For a FortiGate used by both Oxidized and Profylaxia, extend the same existing
secret rather than creating another credential path:

```json
{
  "username": "<device-account>",
  "password": "<device-password>",
  "api_token": "<read-only-fortigate-api-token>"
}
```

Oxidized consumes only the SSH fields. The Ansible-backed FortiOS Profylaxia
collector consumes only `api_token`. Adding this field is a one-time operator
upgrade for existing Fortinet profiles; Atlas deployment does not create or
rotate device API tokens.

Optional `enable_password` is a string and is not required. `admin` is the purpose for Oxidized; `snmp` is separate. The path helper also allows the future `automation` purpose without changing the hierarchy.

SNMPv2c:

```json
{"version": "2c", "community": "<community>"}
```

SNMPv3 (authPriv shape):

```json
{
  "version": "3",
  "username": "<snmp-account>",
  "auth_protocol": "SHA256",
  "auth_password": "<auth-secret>",
  "privacy_protocol": "AES",
  "privacy_password": "<privacy-secret>"
}
```

The shape validator checks required nonempty strings. Protocol labels are stored
as data; this does not assert collector support for every algorithm. Zabbix
reconciliation currently accepts SNMPv2c and explicitly reports
`unsupported_snmp_version` for SNMPv3. Enabling authPriv/authNoPriv requires an
explicit interface-details mapping and tests; no protocol fallback is used.

## Runtime contract and limits

`atlas/device_credentials.py` is shared by the FastAPI and deployment layers:

- `oxidized_reference(device)` ignores devices whose flag is not the boolean `true`; validates an enabled Device's platform, service mapping, explicit profile, vendor slug, name and IP.
- `resolve_oxidized_device(device, client, token)` reads the exact `admin` path from OpenBao and validates username/password. The return object's representation hides credentials. Callers must not serialize or log its credential dictionary.
- Missing platform, missing mapping, missing profile, missing secret and invalid admin schema have distinct errors. Permission/transport failures are distinct from missing secrets. There is no default credential/profile or platform inference.
- OpenBao HTTP exceptions omit response bodies and transport exception details to avoid propagating secret-bearing diagnostics.

Example:

```text
SW-CORE-01
NetBox: manufacturer.slug=cisco, platform.slug=cisco-ios-xe
        credential_profile=cisco-default, oxidized_enabled=true
Atlas:  cisco-ios-xe -> ios
Bao:    atlas / devices/credentials/cisco/cisco-default/admin
Consumers: Oxidized model=ios; Zabbix template=Cisco IOS by SNMP
```

`atlasctl oxidized reconcile` and `atlasctl zabbix sync` both query NetBox as
the source of truth and resolve only the referenced credentials from OpenBao.
Oxidized receives an ephemeral JSON inventory; Zabbix receives a secret macro
through its official API. Neither consumer becomes an authoritative inventory
or credential store.

## Operator steps

1. Review and deploy this branch using the existing installation/update process, then run `sudo atlasctl deploy`.
2. Review `default_platform` on imported Device Types and audit the unmapped list. Existing Devices with no platform need an explicit operator-selected platform; no bulk backfill runs.
3. In the existing local OpenBao access flow (`sudo atlasctl openbao`, SSH tunnel), create the real `admin` and optionally `snmp` secrets at the exact profile paths. Do not paste real secrets into Git, command history, reports or NetBox.
4. Set the explicit `credential_profile` on each Device and its intended platform/IP/enable flags. A shared profile is deliberately selected, never guessed from the name.
5. If operator-managed legacy secrets exist at old `devices/credentials/default` or `devices/credentials/cisco` paths, copy them through the authenticated OpenBao UI and verify consumers before retiring old keys. Atlas-generated monitoring values under `devices/snmp/<vendor>/<device-id>/monitoring` are different: an approved `provision_and_monitor` operation copies a valid value with `cas=0` to `devices/credentials/<vendor>/<device-name>/snmp`. It never deletes the legacy value. The monitoring provisioner can write only that terminal `snmp` leaf and cannot write sibling `admin` credentials.
6. Run `sudo atlasctl oxidized reconcile` and `sudo atlasctl zabbix sync`, then
   perform physical-device acceptance tests before enabling any newly verified
   driver family.

## Verification and evidence

Local unit tests cover repeated provisioning with no extra writes, read-only dry-run, targeted default-platform updates, removing a mapping, preservation of non-platform fields, custom-field idempotence, stale-build detection, actual model aliases, conflict rejection, all required missing-data failures, path traversal, hostname rename, optional enable password, SNMP shape and error redaction.

GitHub CI additionally tests the declared model files **inside the deployed image**, fresh NetBox provisioning, native Device platform inheritance, a manual Device override, stable IDs after redeployment and real OpenBao credential reads / missing-profile behavior. The complete existing deployment, ingress and upgrade checks remain enabled. CI results must be read from the PR; this document does not imply a test passed before it ran.

Primary evidence reviewed 2026-10-16:

- [Pinned community Device Type Library](https://github.com/netbox-community/devicetype-library/tree/517549215455824be5e3b89965353fd2210cc20c/device-types) — actual models/slugs and vendor datasheet links.
- [NetBox v4.7.1 model implementation](https://github.com/netbox-community/netbox/blob/v4.7.1/netbox/dcim/models/devices.py) — native defaults and inheritance.
- [Oxidized 0.37.0 model list](https://github.com/ytti/oxidized/blob/0.37.0/docs/Supported-OS-Types.md), [AOS-CX](https://github.com/ytti/oxidized/blob/0.37.0/lib/oxidized/model/aoscx.rb), [ProCurve](https://github.com/ytti/oxidized/blob/0.37.0/lib/oxidized/model/procurve.rb), [IOS](https://github.com/ytti/oxidized/blob/0.37.0/lib/oxidized/model/ios.rb), [NX-OS](https://github.com/ytti/oxidized/blob/0.37.0/lib/oxidized/model/nxos.rb), [Junos](https://github.com/ytti/oxidized/blob/0.37.0/lib/oxidized/model/junos.rb), [CiscoSMB](https://github.com/ytti/oxidized/blob/0.37.0/lib/oxidized/model/ciscosmb.rb) — source commit `3ff7335d85592993727f7dfa7a98cd70aedb87b0`.
- [HPE Aruba AOS-CX 10.15 supported platforms](https://www.arubanetworks.com/techdocs/AOS-CX/10.15/PDF/security_4100i-6000-6100.pdf) and [AOS-CX 10.14 platform guide](https://www.arubanetworks.com/techdocs/AOS-CX/10.14/PDF/vxlan.pdf) — reviewed switch-family OS boundary.
- [Zabbix 7.0 Aruba CX 8300s template](https://git.zabbix.com/projects/ZBX/repos/zabbix/browse/templates/net/aruba/aruba_cx8300s_snmp?at=release%2F7.0) — reason for retaining the generic SNMP template across the broader Aruba selection.
- [Cisco Catalyst 9000 release guidance](https://www.cisco.com/c/en/us/support/docs/switches/catalyst-9300-series-switches/214814-recommended-releases-for-catalyst-9200-9.html), [3850 IOS XE release notes](https://www.cisco.com/c/en/us/support/switches/catalyst-3850-series-switches/products-release-notes-list.html), [8200/8300 IOS XE releases](https://www.cisco.com/c/en/us/support/routers/catalyst-8300-series-edge-platforms/series.html).
- [Juniper QFX5130 system overview](https://www.juniper.net/documentation/us/en/hardware/qfx5130/topics/topic-map/qfx5130-system-overview.html) — Junos OS Evolved, reason for avoiding a broad QFX rule; [QFX5200](https://www.juniper.net/documentation/product/us/en/qfx5200/), [EX4100](https://www.juniper.net/documentation/product/us/en/ex4100/), [EX4000 hardware compatibility](https://pathfinder.juniper.net/hct/product/EX4000).

Confidence: high for pinned code behavior and reproducible catalog counts; medium for family-level default-platform rules (reviewed hardware identity, not discovery of a running OS); unverified for physical-device collection and deliberately unmapped families. Vendor and driver support are version-sensitive; review sources and mappings whenever images/catalog pins change.
