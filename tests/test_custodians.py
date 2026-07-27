from __future__ import annotations

import json
import unittest
from dataclasses import FrozenInstanceError

from simplicio_fast.custodians import (
    ADDRESS_SCHEMA,
    CATALOG_SCHEMA,
    CUSTODIAN_CATALOG,
    CUSTODIAN_ROLES,
    CUSTODIANS,
    VIRTUAL_POINTER_KIND,
    CustodianAddressV1,
    catalog_json,
)


class CustodianContractTest(unittest.TestCase):
    def test_catalog_has_exact_roles_and_required_metadata(self) -> None:
        expected = {
            "IndexGenerationSteward",
            "CacheIntegritySentinel",
            "KnowledgeFederationSteward",
            "PythonRustParityAuditor",
        }
        self.assertEqual(expected, set(CUSTODIAN_ROLES))
        self.assertEqual(expected, set(CUSTODIANS))
        self.assertEqual(4, len(CUSTODIAN_CATALOG))
        for address in CUSTODIAN_CATALOG:
            self.assertEqual(ADDRESS_SCHEMA, address.schema)
            self.assertEqual(VIRTUAL_POINTER_KIND, address.pointer_kind)
            self.assertTrue(address.virtual)
            self.assertTrue(address.owner)
            self.assertTrue(address.endpoint)
            for metadata in (address.capabilities, address.inputs, address.outputs, address.invariants):
                self.assertIsInstance(metadata, tuple)
                self.assertTrue(metadata)
                self.assertTrue(all(isinstance(item, str) for item in metadata))

    def test_addresses_are_immutable(self) -> None:
        address = CUSTODIAN_CATALOG[0]
        with self.assertRaises(FrozenInstanceError):
            address.owner = "another-owner"  # type: ignore[misc]
        with self.assertRaises(FrozenInstanceError):
            address.capabilities += ("new-capability",)  # type: ignore[misc]
        with self.assertRaises(TypeError):
            CUSTODIANS[address.role] = address  # type: ignore[index]

    def test_serialization_is_deterministic_and_json_safe(self) -> None:
        first = catalog_json()
        second = catalog_json()
        self.assertEqual(first, second)
        decoded = json.loads(first)
        self.assertEqual(CATALOG_SCHEMA, decoded["schema"])
        self.assertEqual(4, len(decoded["custodians"]))
        self.assertEqual(first, json.dumps(decoded, sort_keys=True, separators=(",", ":")))
        self.assertEqual(first, json.dumps(json.loads(first), sort_keys=True, separators=(",", ":")))
        for address in CUSTODIAN_CATALOG:
            self.assertEqual(address.to_json(), json.dumps(address.to_dict(), sort_keys=True, separators=(",", ":")))

    def test_invalid_contract_values_are_rejected(self) -> None:
        valid = CUSTODIAN_CATALOG[0]
        invalid_values = (
            {"role": "UnknownRole"},
            {"endpoint": "https://worker.example"},
            {"owner": ""},
            {"capabilities": []},
            {"inputs": ()},
            {"outputs": ("",)},
            {"invariants": ("same", "same")},
            {"pointer_kind": "worker"},
            {"virtual": False},
        )
        for changes in invalid_values:
            with self.subTest(changes=changes), self.assertRaises((TypeError, ValueError)):
                CustodianAddressV1(
                    role=changes.get("role", valid.role),
                    endpoint=changes.get("endpoint", valid.endpoint),
                    owner=changes.get("owner", valid.owner),
                    capabilities=changes.get("capabilities", valid.capabilities),
                    inputs=changes.get("inputs", valid.inputs),
                    outputs=changes.get("outputs", valid.outputs),
                    invariants=changes.get("invariants", valid.invariants),
                    pointer_kind=changes.get("pointer_kind", valid.pointer_kind),
                    virtual=changes.get("virtual", valid.virtual),
                )


if __name__ == "__main__":
    unittest.main()
