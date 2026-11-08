# Contribuir

Abra uma issue com o comportamento esperado e um contrato mínimo fictício. Para correções, inclua um teste que falhe antes da mudança e passe depois dela.

```bash
python -m unittest discover -s tests -v
python -m http_contract_diff examples/before.json examples/compatible.json
```

O segundo exemplo termina com `0`; `examples/breaking.json` deve terminar com `1`. Dados inválidos devem terminar com `2`, sem resultado parcial.

Mantenha a validação integral, a ordem determinística e a distinção entre compatibilidade modelada e comportamento real. Novos tipos ou keywords precisam de semântica documentada, limites e testes de direção. Não acrescente heurísticas que aceitem silenciosamente um schema desconhecido.

Não inclua tokens, URLs privadas, contratos de clientes, dados de produção ou relatórios confidenciais. As contribuições são distribuídas pela licença MIT deste repositório.
