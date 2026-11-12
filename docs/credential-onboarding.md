# Device credential onboarding

Atlas Operations UI provides an explicit plan/apply flow for device credentials.
One plan targets one or more active devices from one tenant and the same
manufacturer. The operator first selects the tenant, can narrow the list to one
site, and chooses one shared credential profile for the whole batch. The default
mode assigns one explicit platform. The optional mixed-platform mode preserves
the existing platform of every Device and requires every selected platform to
support the requested consumer and authentication type. Atlas blocks devices
without a tenant, cross-tenant batches, missing platforms in mixed mode, and a
device that already has a different platform in explicit mode.
It stores only `credential_profile`, the optional selected platform,
enablement flags and the selected Profylaxia mode in NetBox. Passwords, API
tokens and SNMP communities are written only to the OpenBao KV v2 mount.

## Paths and consumers

```text
devices/credentials/<manufacturer.slug>/<credential_profile>/prophylaxis
devices/credentials/<manufacturer.slug>/<credential_profile>/oxidized
devices/credentials/<manufacturer.slug>/<credential_profile>/snmp
```

- `prophylaxis`: SSH `username`, `password`, optional `enable_password`; or a
  FortiOS `api_token` when the exact `fortios-api` NetBox service is selected.
- `oxidized`: dedicated SSH `username` and `password`.
- `snmp`: SNMPv2c `community` in the first implementation.

There is no fallback between purpose leaves. The browser sends secret values
only in the authorized plan/apply requests. Atlas never returns them in the
response, logs them, or stores them in NetBox.

The explicit `Reuse an existing OpenBao profile` action does not accept or
read secret values. Its dry-run checks only KV v2 metadata for each selected
purpose, records the observed version in the HMAC-bound plan, and blocks apply
when the exact purpose leaf is missing. Apply rechecks that metadata and then
updates only the selected NetBox device fields; OpenBao is not written.

Profylaxia exposes two collection modes: `identity` and `full_collection`.
The operator can select either mode or both while assigning the profile.

## Safety contract

1. Select one tenant and one or more active NetBox devices from that tenant and
   the same manufacturer, a shared credential profile, and either one explicit
   compatible platform or the option to preserve all existing platforms. A
   plan accepts at most 100 unique devices.
2. Select the consumers and collection mode. Either enter purpose-specific
   values or explicitly choose reuse of an existing profile.
3. Create a dry-run plan. An existing different value is a conflict unless
   explicit replacement was selected.
4. Confirm and apply the exact plan. The plan fingerprint is HMAC-bound to the
   operator token and the complete request, including the secret values.
5. OpenBao writes use KV v2 compare-and-set. New values use `cas=0`; replacements
   use the version observed during planning.

The dedicated `atlas-credential-writer` AppRole can create, read and update only
the three exact device credential leaves. It cannot list profiles, delete
credentials or access unrelated OpenBao paths.

FortiOS transport remains deterministic: an exact `fortios-api` service selects
HTTPS API; absence selects SSH. Atlas blocks ambiguous or conflicting service
state and does not silently fall back.
