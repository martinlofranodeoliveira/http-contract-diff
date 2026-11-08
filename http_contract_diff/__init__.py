"""Comparação offline de um perfil explícito de contratos HTTP planos."""

from .core import ContractError, compare_contracts, load_contract, validate_contract

__all__ = ["ContractError", "compare_contracts", "load_contract", "validate_contract"]
__version__ = "1.0.0"
