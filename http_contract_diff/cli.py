import argparse
import json
import sys

from .core import ContractError, compare_contracts, load_contract


def main(argv=None):
    parser = argparse.ArgumentParser(description="Compara contratos HTTP JSON versão 1, offline.")
    parser.add_argument("old", help="Contrato antigo JSON UTF-8")
    parser.add_argument("new", help="Contrato novo JSON UTF-8")
    args = parser.parse_args(argv)
    try:
        report = compare_contracts(load_contract(args.old), load_contract(args.new))
    except ContractError as exc:
        print("Contrato inválido: " + str(exc), file=sys.stderr)
        return 2
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if report["compatible"] else 1
