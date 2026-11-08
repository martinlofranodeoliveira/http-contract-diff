"""Validação e comparação conservadora, sem rede ou interpretação de schemas externos."""

from dataclasses import dataclass
from decimal import Decimal, DecimalException
import json
import math
from pathlib import Path
import re

MAX_BYTES = 1_048_576
MAX_ROUTES = 500
MAX_FIELDS = 10_000
MAX_ENUM = 128
METHODS = frozenset({"GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"})
TYPES = frozenset({"string", "integer", "number", "boolean"})


class ContractError(ValueError):
    """Documento inválido ou fora do perfil suportado; nenhum diff é produzido."""


@dataclass(frozen=True)
class _Field:
    type: str
    required: bool
    enum: frozenset | None


@dataclass(frozen=True)
class _Route:
    method: str
    path: str
    request: tuple
    responses: tuple


@dataclass(frozen=True)
class _Contract:
    routes: tuple


def _object(value, required, optional=()):
    if not isinstance(value, dict) or set(value) - (set(required) | set(optional)) or set(required) - set(value):
        raise ContractError("Objeto com campos ausentes, desconhecidos ou formato não suportado.")


def _name(value, maximum=256):
    if not isinstance(value, str) or not value or len(value) > maximum or any(ord(c) < 32 or ord(c) == 127 or 0xD800 <= ord(c) <= 0xDFFF for c in value):
        raise ContractError("Nome vazio, longo demais ou com caracteres de controle.")
    return value


def _enum_value(value, declared):
    if declared == "string":
        if not isinstance(value, str) or len(value) > 4096:
            raise ContractError("Enum incompatível com o tipo string ou longo demais.")
        return ("string", value)
    if declared == "boolean":
        if type(value) is not bool:
            raise ContractError("Enum incompatível com o tipo boolean.")
        return ("boolean", value)
    if declared == "integer":
        if type(value) is not int:
            raise ContractError("Enum incompatível com o tipo integer.")
        return ("integer", value)
    if type(value) not in (int, float, Decimal):
        raise ContractError("Enum incompatível com o tipo number.")
    if type(value) is float and not math.isfinite(value):
        raise ContractError("Números não finitos não são permitidos.")
    number = value if isinstance(value, Decimal) else Decimal(str(value))
    if not number.is_finite():
        raise ContractError("Números não finitos não são permitidos.")
    return ("number", number)


def _fields(value, budget):
    _object(value, ("fields",))
    if not isinstance(value["fields"], dict):
        raise ContractError("fields deve ser um objeto.")
    budget[0] += len(value["fields"])
    if budget[0] > MAX_FIELDS:
        raise ContractError("Limite total de campos excedido.")
    compiled = []
    for name, schema in value["fields"].items():
        _name(name)
        _object(schema, ("type", "required"), ("enum",))
        if not isinstance(schema["type"], str) or schema["type"] not in TYPES or type(schema["required"]) is not bool:
            raise ContractError("Tipo ou obrigatoriedade do campo não suportado.")
        values = None
        if "enum" in schema:
            raw = schema["enum"]
            if not isinstance(raw, list) or not 1 <= len(raw) <= MAX_ENUM:
                raise ContractError("enum deve conter de 1 a 128 valores.")
            values = frozenset(_enum_value(v, schema["type"]) for v in raw)
            if len(values) != len(raw):
                raise ContractError("enum não permite valores repetidos.")
        compiled.append((name, _Field(schema["type"], schema["required"], values)))
    return tuple(sorted(compiled))


def validate_contract(document):
    """Valida integralmente e cria um snapshot imutável do perfil versão 1.

    A API aceita objetos Python já decodificados. Para JSON sem chaves repetidas
    e com limite de bytes, use load_contract().
    """
    _object(document, ("version", "routes"))
    if type(document["version"]) is not int or document["version"] != 1:
        raise ContractError("Apenas version 1 é suportada.")
    routes = document["routes"]
    if not isinstance(routes, list) or len(routes) > MAX_ROUTES:
        raise ContractError("routes deve ser uma lista de até 500 operações.")
    result, seen, budget = [], set(), [0]
    for route in routes:
        _object(route, ("method", "path", "request", "responses"))
        method, path = route["method"], _name(route["path"], 2048)
        if not isinstance(method, str) or method not in METHODS:
            raise ContractError("Método HTTP não suportado; use letras maiúsculas.")
        if not path.startswith("/") or any(c.isspace() for c in path) or "?" in path or "#" in path:
            raise ContractError("path deve ser um caminho absoluto sem espaço, query ou fragmento.")
        identity = (method, path)
        if identity in seen:
            raise ContractError("Operação HTTP repetida.")
        seen.add(identity)
        request = _fields(route["request"], budget)
        responses = route["responses"]
        if not isinstance(responses, dict) or not responses:
            raise ContractError("responses deve declarar pelo menos um status HTTP.")
        compiled_responses = []
        for status, response in responses.items():
            if not isinstance(status, str) or not re.fullmatch(r"[1-5][0-9]{2}", status):
                raise ContractError("Status deve ser uma string HTTP explícita, de 100 a 599.")
            compiled_responses.append((status, _fields(response, budget)))
        result.append(_Route(method, path, request, tuple(sorted(compiled_responses))))
    return _Contract(tuple(sorted(result, key=lambda r: (r.path, r.method))))


def _pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ContractError("JSON com chave repetida.")
        result[key] = value
    return result


def _constant(_):
    raise ContractError("JSON com número não finito.")


def load_contract(filename):
    """Lê no máximo 1 MiB, rejeita duplicatas/NaN e valida todo o contrato."""
    try:
        with Path(filename).open("rb") as stream:
            data = stream.read(MAX_BYTES + 1)
        if len(data) > MAX_BYTES:
            raise ContractError("Arquivo excede o limite de 1 MiB.")
        document = json.loads(data.decode("utf-8"), object_pairs_hook=_pairs,
                              parse_float=Decimal, parse_constant=_constant)
        return validate_contract(document)
    except ContractError:
        raise
    except (OSError, UnicodeError, ValueError, RecursionError, OverflowError, DecimalException) as exc:
        raise ContractError("Não foi possível ler um contrato JSON UTF-8 válido.") from exc


def _pointer(value):
    return value.replace("~", "~0").replace("/", "~1")


def _field_changes(old, new, prefix, response, emit):
    before, after = dict(old), dict(new)
    for name in sorted(before.keys() | after.keys()):
        a, b = before.get(name), after.get(name)
        where = prefix + "/fields/" + _pointer(name)
        if a is None:
            emit(where, "field_added", not response and b.required)
            continue
        if b is None:
            emit(where, "field_removed", not response or a.required)
            continue
        if a.required != b.required:
            emit(where + "/required", "required_changed", (a.required and not b.required) if response else (not a.required and b.required))
        if a.type != b.type:
            # Deliberately conservative: even integer -> number is reported.
            emit(where + "/type", "type_changed", True)
        elif a.enum != b.enum:
            # None means the whole declared primitive domain.
            safe = (a.enum is None or (b.enum is not None and b.enum <= a.enum)) if response else (b.enum is None or (a.enum is not None and a.enum <= b.enum))
            emit(where + "/enum", "enum_changed", not safe)


def compare_contracts(old, new):
    """Compara NEW contra consumidores do OLD; nunca consulta endpoints.

    Argumentos podem ser documentos dict ou snapshots de validate/load.
    Ambos os documentos são validados antes de qualquer resultado ser emitido.
    """
    old = old if isinstance(old, _Contract) else validate_contract(old)
    new = new if isinstance(new, _Contract) else validate_contract(new)
    before = {(r.method, r.path): r for r in old.routes}
    after = {(r.method, r.path): r for r in new.routes}
    changes = []
    for identity in sorted(before.keys() | after.keys(), key=lambda x: (x[1], x[0])):
        method, path = identity
        a, b = before.get(identity), after.get(identity)
        def emit(location, code, breaking):
            changes.append({"method": method, "path": path, "location": location,
                            "code": code, "severity": "breaking" if breaking else "compatible"})
        if a is None:
            emit("/", "operation_added", False)
            continue
        if b is None:
            emit("/", "operation_removed", True)
            continue
        _field_changes(a.request, b.request, "/request", False, emit)
        responses_before, responses_after = dict(a.responses), dict(b.responses)
        for status in sorted(responses_before.keys() | responses_after.keys()):
            prefix = "/responses/" + status
            if status not in responses_before:
                emit(prefix, "status_added", True)
            elif status not in responses_after:
                emit(prefix, "status_removed", True)
            else:
                _field_changes(responses_before[status], responses_after[status], prefix, True, emit)
    changes.sort(key=lambda c: (c["path"], c["method"], c["location"], c["code"]))
    breaking = sum(c["severity"] == "breaking" for c in changes)
    return {"profile_version": 1, "compatible": not breaking,
            "summary": {"breaking": breaking, "compatible": len(changes) - breaking},
            "changes": changes}
