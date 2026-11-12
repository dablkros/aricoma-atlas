# Cisco command and parser registry

Atlas selects Cisco raw commands and their parsers from
`catalog/prophylaxis/cisco_commands.yaml`. The YAML catalog is the source of
truth; the compatibility constants exposed by `cisco_raw.py` are generated
views of that catalog.

## Data flow

`NetBox platform -> platform profile -> registry resolver -> Ansible cli_command -> raw stdout -> explicit parser -> structured_data -> optional normalized_data -> Site report`

The resolver does not infer an operating system from a manufacturer or model.
It accepts only the reviewed Atlas platform slugs listed by the registry.

## Registry contract

Each stable command ID defines a category, collection modes, ordering priority,
and one or more platform variants. A variant contains the actual CLI command,
an optional compatibility constraint, an explicit parser contract, and separate
documentation/fixture/device verification evidence.

Parser engines:

- `ntc_textfsm`: loads the exact packaged `.textfsm` resource named in YAML.
- `atlas_custom`: calls a reviewed function registered in `_CUSTOM_PARSERS`.
- `none`: preserves raw output without attempting parsing.

The execution command is never used as an implicit template selector.

## Adding a command

1. Add one stable ID and its platform variants to the YAML catalog.
2. Use `identity`, `full_collection`, or both modes.
3. Mark unsupported platform variants absent or explicitly `enabled: false`.
4. Assign an exact NTC template only after confirming that the template matches
   the target output. Otherwise use `none` and keep raw collection available.
5. Copy the template's `Value` names, lower-cased, into `output_fields`.
6. Add a trusted fixture test. Only set `real_device_tested: true` after a real
   model/OS capture has been verified, and record the evidence.

For a custom parser, add the function to `cisco_raw.py`, register its exact name
in `_CUSTOM_PARSERS`, declare that function in YAML, and test both valid and
empty/malformed output. Missing values remain `null` or absent; parsers must not
invent zero counters.

## Result semantics

Execution and parsing are independent:

| Layer | Values | Meaning |
|---|---|---|
| execution `status` | `success`, `unsupported`, `permission_denied`, `timeout`, `empty`, `error`, `truncated` | Result of running the command |
| parsing `parsing_status` | `success`, `no_parser`, `empty_result`, `parse_error`, `skipped` | Result of the optional parser |
| legacy `parser_status` | `parsed`, `template_missing`, `failed`, `not_requested` | Preserved report/API compatibility |

A parser failure never changes successful command execution or removes raw
stdout. Historical error text returned by `show logging` is not treated as an
execution failure. Commands with `none` report `no_parser` and retain raw data.

Reports record the actual `parser_engine`, `parser_platform`, `parser_template`,
and `parser_version`. `normalized_data` is emitted only for reviewed common
fields; `structured_data` and redacted raw stdout remain available.

## Configuration safety

`show running-config` remains part of reviewed collection, but its raw stdout
passes through the existing line and private-key redaction before report
storage. The registry does not add logging, API, or LLM export paths. Adding a
new secret-bearing syntax requires extending redaction tests before enabling the
command.

## Verification and NTC updates

Atlas pins `ntc-templates==9.3.0` and `textfsm==2.1.0`. The implementation uses
Python's documented package-resource API and TextFSM directly, not an internal
NTC selector. Primary references reviewed for this contract:

- [NTC Templates 9.3.0 release](https://github.com/networktocode/ntc-templates/releases/tag/v9.3.0)
- [NTC template index](https://github.com/networktocode/ntc-templates/blob/v9.3.0/ntc_templates/templates/index)
- [NTC programmatic parsing guide](https://github.com/networktocode/ntc-templates/blob/v9.3.0/docs/user/lib_getting_started.md)
- [Python `importlib.resources`](https://docs.python.org/3/library/importlib.resources.html)

When upgrading NTC Templates, compare all declared template files and `Value`
headers, run the registry and parser fixtures, and update the pinned version and
catalog version together. Template existence alone is not device compatibility.

## Generated support overview

Do not maintain a second manual matrix. Generate Markdown or JSON directly from
the validated catalog:

```bash
python3 scripts/render_cisco_command_registry.py --format markdown
python3 scripts/render_cisco_command_registry.py --format json
```

The output columns are `platform | command | parser | output_fields | verification`.
