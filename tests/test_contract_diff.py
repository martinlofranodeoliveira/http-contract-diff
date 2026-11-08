from copy import deepcopy
from decimal import Decimal
from itertools import combinations
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from http_contract_diff import ContractError, compare_contracts, load_contract, validate_contract
from http_contract_diff.core import MAX_BYTES

ROOT = Path(__file__).resolve().parents[1]


def field(required=False, enum=None, kind="string"):
    result = {"type": kind, "required": required}
    if enum is not None:
        result["enum"] = enum
    return result


def contract(request=None, response=None):
    return {"version": 1, "routes": [{"method": "GET", "path": "/items",
            "request": {"fields": request or {}},
            "responses": {"200": {"fields": response or {}}}}]}


class ComparisonTests(unittest.TestCase):
    def test_identical_has_no_changes_and_does_not_mutate_inputs(self):
        original = contract({"query": field()}, {"id": field(True)})
        saved = deepcopy(original)
        self.assertEqual(compare_contracts(original, original)["changes"], [])
        self.assertEqual(original, saved)

    def test_required_request_and_promised_response_have_opposite_variance(self):
        optional = contract({"q": field()}, {"id": field()})
        required = contract({"q": field(True)}, {"id": field(True)})
        changes = compare_contracts(optional, required)["changes"]
        self.assertEqual([(c["location"], c["severity"]) for c in changes],
                         [("/request/fields/q/required", "breaking"),
                          ("/responses/200/fields/id/required", "compatible")])
        reverse = compare_contracts(required, optional)["changes"]
        self.assertEqual([c["severity"] for c in reverse], ["compatible", "breaking"])

    def test_finite_domain_oracle_checks_all_enum_subset_pairs_in_both_directions(self):
        # Independent evaluator over a domain containing an outsider. For this
        # string-only enum family that is sufficient to distinguish unbounded.
        domain = {"a", "b", "c", "outside"}
        enums = [None] + [list(c) for n in range(1, 4) for c in combinations(["a", "b", "c"], n)]
        for a in enums:
            for b in enums:
                accepted_old = domain if a is None else set(a)
                accepted_new = domain if b is None else set(b)
                request = compare_contracts(contract({"value": field(enum=a)}), contract({"value": field(enum=b)}))
                response = compare_contracts(contract(response={"value": field(enum=a)}), contract(response={"value": field(enum=b)}))
                self.assertEqual(request["compatible"], accepted_old <= accepted_new, (a, b, "request"))
                self.assertEqual(response["compatible"], accepted_new <= accepted_old, (a, b, "response"))

    def test_removing_request_fields_breaks_but_optional_response_removal_does_not(self):
        self.assertFalse(compare_contracts(contract({"q": field()}), contract())["compatible"])
        self.assertFalse(compare_contracts(contract(response={"id": field(True)}), contract())["compatible"])
        self.assertTrue(compare_contracts(contract(response={"hint": field()}), contract())["compatible"])

    def test_additional_optional_request_and_additional_response_fields_are_compatible(self):
        before = contract()
        self.assertTrue(compare_contracts(before, contract({"q": field()}, {"id": field(True)}))["compatible"])
        self.assertFalse(compare_contracts(before, contract({"q": field(True)}))["compatible"])

    def test_operation_and_response_status_changes_are_reported(self):
        before = contract()
        after = deepcopy(before)
        after["routes"][0]["responses"] = {"201": {"fields": {}}}
        changes = compare_contracts(before, after)["changes"]
        self.assertEqual([c["code"] for c in changes], ["status_removed", "status_added"])
        self.assertEqual(compare_contracts(before, {"version": 1, "routes": []})["summary"]["breaking"], 1)
        self.assertTrue(compare_contracts({"version": 1, "routes": []}, before)["compatible"])

    def test_type_changes_are_conservative_and_no_enum_values_are_disclosed(self):
        before = contract({"value": field(enum=["DO_NOT_PRINT_VALUE"])})
        after = contract({"value": field(kind="number")})
        result = compare_contracts(before, after)
        self.assertFalse(result["compatible"])
        self.assertNotIn("DO_NOT_PRINT_VALUE", json.dumps(result))
        self.assertEqual(result["changes"][0]["code"], "type_changed")

    def test_result_is_deterministic_under_route_field_and_enum_reordering(self):
        before = contract({"x": field(enum=["a", "b"]), "y": field()})
        after = contract({"y": field(True), "x": field(enum=["a"])})
        other = deepcopy(after)
        other["routes"][0]["request"]["fields"] = dict(reversed(list(other["routes"][0]["request"]["fields"].items())))
        self.assertEqual(compare_contracts(before, after), compare_contracts(before, other))
        reordered = deepcopy(before)
        reordered["routes"][0]["request"]["fields"]["x"]["enum"].reverse()
        self.assertEqual(compare_contracts(before, reordered)["changes"], [])

    def test_compiled_snapshot_cannot_change_when_original_dict_is_edited(self):
        original = contract({"q": field()})
        snapshot = validate_contract(original)
        original["routes"].clear()
        self.assertEqual(compare_contracts(snapshot, snapshot)["changes"], [])
        self.assertEqual(compare_contracts(snapshot, original)["changes"][0]["code"], "operation_removed")

    def test_locations_escape_json_pointer_field_names(self):
        result = compare_contracts(contract(), contract({"a/b~c": field(True)}))
        self.assertEqual(result["changes"][0]["location"], "/request/fields/a~1b~0c")


class ValidationTests(unittest.TestCase):
    def test_unknown_keywords_and_nested_schemas_fail_before_any_diff(self):
        for extra in ["nullable", "items", "minimum", "$ref"]:
            value = contract({"v": field()})
            value["routes"][0]["request"]["fields"]["v"][extra] = True
            with self.assertRaises(ContractError):
                compare_contracts(contract(), value)
        value = contract({"v": {"type": "object", "required": False}})
        with self.assertRaises(ContractError):
            validate_contract(value)

    def test_enum_types_do_not_confuse_bool_integer_and_number(self):
        for kind, values in [("integer", [True]), ("integer", [1.0]), ("boolean", [1]), ("number", [True]),
                             ("number", [float("nan")]), ("number", [float("inf")]), ("string", [None])]:
            with self.subTest(kind=kind, values=values):
                with self.assertRaises(ContractError):
                    validate_contract(contract({"v": field(enum=values, kind=kind)}))
        validate_contract(contract({"v": field(enum=[1, Decimal("1.5")], kind="number")}))
        with self.assertRaises(ContractError):
            validate_contract(contract({"v": field(enum=[1, 1.0], kind="number")}))

    def test_root_version_duplicate_operations_and_invalid_status_are_rejected(self):
        cases = []
        value = contract(); value["version"] = True; cases.append(value)
        value = contract(); value["routes"].append(deepcopy(value["routes"][0])); cases.append(value)
        value = contract(); value["routes"][0]["responses"] = {"default": {"fields": {}}}; cases.append(value)
        value = contract(); value["unknown"] = 1; cases.append(value)
        for value in cases:
            with self.assertRaises(ContractError):
                validate_contract(value)

    def test_file_parser_rejects_duplicate_json_keys_and_non_finite_numbers(self):
        bodies = ['{"version":1,"version":1,"routes":[]}',
                  '{"version":1,"routes":[],"extra":NaN}',
                  '{"version":1,"routes":[{"method":"GET","path":"/","request":{"fields":{}},"responses":{"200":{"fields":{"v":{"type":"string","required":false,"enum":["a"],"enum":["b"]}}}}}]}']
        with tempfile.TemporaryDirectory() as temp:
            file = Path(temp, "input.json")
            for body in bodies:
                file.write_text(body, encoding="utf-8")
                with self.assertRaises(ContractError):
                    load_contract(file)

    def test_input_limit_and_utf8_are_enforced(self):
        with tempfile.TemporaryDirectory() as temp:
            file = Path(temp, "input.json")
            file.write_bytes(b" " * (MAX_BYTES + 1))
            with self.assertRaisesRegex(ContractError, "1 MiB"):
                load_contract(file)
            file.write_bytes(b"\xff\xfe")
            with self.assertRaises(ContractError):
                load_contract(file)

    def test_unpaired_surrogate_names_and_excessive_numeric_exponents_are_rejected(self):
        value = contract({"bad\ud800": field()})
        with self.assertRaises(ContractError):
            validate_contract(value)
        with tempfile.TemporaryDirectory() as temp:
            file = Path(temp, "input.json")
            file.write_text('{"version":1,"routes":[],"untrusted":1e999999999999999999999999}', encoding="utf-8")
            result = CliTests().invoke(file, file)
            self.assertEqual(result.returncode, 2)
            self.assertNotIn("Traceback", result.stderr)

    def test_json_decimal_enum_values_keep_exact_distinctness(self):
        document = json.dumps(contract({"v": field(enum=["REPLACE"], kind="number")}))
        with tempfile.TemporaryDirectory() as temp:
            before, after = Path(temp, "before.json"), Path(temp, "after.json")
            before.write_text(document.replace('"REPLACE"', '0.1'), encoding="utf-8")
            after.write_text(document.replace('"REPLACE"', '0.10000000000000000001'), encoding="utf-8")
            result = compare_contracts(load_contract(before), load_contract(after))
            self.assertFalse(result["compatible"])
            self.assertEqual(result["changes"][0]["code"], "enum_changed")


class CliTests(unittest.TestCase):
    def invoke(self, old, new):
        return subprocess.run([sys.executable, "-m", "http_contract_diff", str(old), str(new)],
                              cwd=ROOT, capture_output=True, text=True)

    def test_cli_exit_codes_no_overwrite_and_determinism(self):
        with tempfile.TemporaryDirectory() as temp:
            old, new = Path(temp, "old.json"), Path(temp, "new.json")
            old.write_text(json.dumps(contract()), encoding="utf-8")
            new.write_text(json.dumps(contract({"name": field(True)})), encoding="utf-8")
            original = (old.read_bytes(), new.read_bytes())
            broken = self.invoke(old, new)
            self.assertEqual(broken.returncode, 1, broken.stderr)
            self.assertEqual(broken.stdout, self.invoke(old, new).stdout)
            self.assertEqual(broken.stderr, "")
            self.assertFalse(json.loads(broken.stdout)["compatible"])
            self.assertEqual(self.invoke(old, old).returncode, 0)
            self.assertEqual((old.read_bytes(), new.read_bytes()), original)
            new.write_text('{"version":true,"routes":[]}', encoding="utf-8")
            invalid = self.invoke(old, new)
            self.assertEqual(invalid.returncode, 2)
            self.assertEqual(invalid.stdout, "")
            self.assertIn("Contrato inválido", invalid.stderr)

    def test_cli_missing_file_is_sanitized_and_does_not_traceback(self):
        result = self.invoke("missing-before.json", "missing-after.json")
        self.assertEqual(result.returncode, 2)
        self.assertEqual(result.stdout, "")
        self.assertNotIn("Traceback", result.stderr)


if __name__ == "__main__":
    unittest.main()
