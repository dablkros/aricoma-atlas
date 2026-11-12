import tempfile
import unittest
from pathlib import Path

from pydantic import ValidationError

from atlas.prophylaxis.cisco_registry import (
    DEFAULT_CISCO_COMMAND_REGISTRY,
    load_cisco_command_registry,
)


class CiscoCommandRegistryTests(unittest.TestCase):
    def test_repository_catalog_is_valid_and_platform_specific(self):
        registry = DEFAULT_CISCO_COMMAND_REGISTRY
        ios = registry.resolve("cisco-ios", "full_collection")
        nxos = registry.resolve("cisco-nxos", "full_collection")
        asa = registry.resolve("cisco-asa", "full_collection")

        self.assertIn("show processes cpu", {item.command for item in ios})
        self.assertIn("show vpc", {item.command for item in nxos})
        self.assertIn("show asp drop", {item.command for item in asa})
        self.assertNotIn("show asp drop", {item.command for item in nxos})

    def test_problematic_commands_have_explicit_templates(self):
        registry = DEFAULT_CISCO_COMMAND_REGISTRY
        ios = registry.command("cisco-ios", "show interfaces transceiver")
        nxos = registry.command("cisco-nxos", "show interface status")

        self.assertEqual(
            "cisco_ios_show_interface_transceiver.textfsm",
            ios.parser.template,
        )
        self.assertEqual(
            "cisco_nxos_show_interface_status.textfsm",
            nxos.parser.template,
        )

    def test_disabled_platform_variant_is_not_resolved(self):
        payload = """
schema_version: 1
ntc_templates_version: 9.3.0
platforms: [cisco-ios, cisco-nxos]
commands:
  - id: stable_identity
    category: device_identity
    priority: 1
    modes: [identity]
    variants:
      - platforms: [cisco-ios]
        command: show version
        enabled: false
        parser: {engine: none}
      - platforms: [cisco-nxos]
        command: show version
        parser: {engine: none}
"""
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "registry.yaml"
            path.write_text(payload, encoding="utf-8")
            registry = load_cisco_command_registry(path)
        self.assertEqual((), registry.resolve("cisco-ios", "identity"))
        self.assertEqual(
            ("show version",),
            tuple(item.command for item in registry.resolve("cisco-nxos", "identity")),
        )

    def test_invalid_parser_contract_is_rejected(self):
        payload = """
schema_version: 1
ntc_templates_version: 9.3.0
platforms: [cisco-ios]
commands:
  - id: stable_identity
    category: device_identity
    priority: 1
    modes: [identity]
    variants:
      - platforms: [cisco-ios]
        command: show version
        parser:
          engine: ntc_textfsm
          platform: cisco_ios
"""
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "registry.yaml"
            path.write_text(payload, encoding="utf-8")
            with self.assertRaises(ValidationError):
                load_cisco_command_registry(path)


if __name__ == "__main__":
    unittest.main()
