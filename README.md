# HTTP Contract Diff

Compare duas versões de um contrato HTTP antes de mudar uma API. O **HTTP Contract Diff** aponta operações removidas, campos que passaram a ser obrigatórios e respostas que deixaram de cumprir o contrato anterior, sem enviar dados ou chamar endpoints.

É uma biblioteca e uma CLI em Python, com saída JSON determinística e um perfil de contrato pequeno e explícito. **Não é um validador OpenAPI ou JSON Schema.** Documentos fora do perfil são recusados integralmente.

## Começar

Requer Python 3.11 ou superior. Não há dependências de runtime.

```bash
git clone https://github.com/martinlofranodeoliveira/http-contract-diff.git
cd http-contract-diff
python -m http_contract_diff examples/before.json examples/compatible.json
```

O exemplo amplia uma opção de entrada e acrescenta campos; termina com código `0` e `compatible: true`.

Para experimentar uma alteração incompatível:

```bash
python -m http_contract_diff examples/before.json examples/breaking.json
```

Esse comando termina intencionalmente com código `1` e aponta quatro mudanças incompatíveis. Não altera os arquivos de entrada.

Para instalar o comando em um ambiente virtual:

```bash
python -m venv .venv
# Linux/macOS:
. .venv/bin/activate
# Windows PowerShell: .venv\Scripts\Activate.ps1
python -m pip install .
http-contract-diff examples/before.json examples/compatible.json
```

O build usa setuptools; a instalação pode precisar obtê-lo do índice de pacotes se ele ainda não estiver disponível no ambiente. A execução da ferramenta não usa rede.

## Formato do contrato

```json
{
  "version": 1,
  "routes": [
    {
      "method": "GET",
      "path": "/catalog",
      "request": {
        "fields": {
          "category": {"type": "string", "required": false, "enum": ["books", "tools"]}
        }
      },
      "responses": {
        "200": {
          "fields": {
            "id": {"type": "string", "required": true}
          }
        }
      }
    }
  ]
}
```

- Uma operação é identificada pelo par exato `method` + `path`. O método deve ser `GET`, `POST`, `PUT`, `PATCH`, `DELETE`, `HEAD` ou `OPTIONS`.
- `path` começa com `/`; não inclui query, fragmento nem espaços. A ferramenta não deduz equivalência entre caminhos ou parâmetros de rota.
- `request.fields` descreve um **objeto lógico plano** de entrada. Não distingue body, query ou headers. Os campos não declarados são considerados recusados pelo servidor.
- `responses` declara pelo menos um status explícito de `100` a `599`, como string. `default` e intervalos de status não são aceitos.
- Cada campo tem `type` e `required`; os tipos possíveis são `string`, `integer`, `number` e `boolean`. `enum` é opcional e, quando presente, contém valores do mesmo tipo declarado.
- Um consumidor antigo pode ignorar campos novos de resposta. Um campo obrigatório de resposta é uma garantia de presença; um campo opcional pode estar ausente.
- Objetos aninhados, arrays, nullabilidade, limites numéricos, formatos, referências, autenticação, headers e outros keywords não são suportados. Não são silenciosamente ignorados.

## O que é considerado incompatível

A direção é sempre **contrato novo contra consumidores do contrato antigo**.

| Mudança | Resultado |
| --- | --- |
| Remover uma operação | Incompatível |
| Adicionar uma operação | Compatível |
| Adicionar ou remover um status de uma operação existente | Incompatível, por política conservadora |
| Remover um campo de entrada | Incompatível: entradas antigas podem conter esse campo |
| Adicionar campo de entrada obrigatório ou tornar um campo obrigatório | Incompatível |
| Adicionar campo de entrada opcional ou torná-lo opcional | Compatível |
| Restringir valores aceitos por um `enum` de entrada | Incompatível |
| Ampliar valores aceitos por um `enum` de entrada | Compatível |
| Remover campo obrigatório de resposta ou torná-lo opcional | Incompatível |
| Remover campo opcional de resposta | Compatível: sua ausência já era permitida |
| Adicionar campo de resposta ou torná-lo obrigatório | Compatível |
| Ampliar valores possíveis de um `enum` de resposta | Incompatível |
| Restringir valores possíveis de um `enum` de resposta | Compatível |
| Trocar um tipo, inclusive `integer` por `number` | Incompatível, por política conservadora |

Ausência de `enum` significa todo o domínio do tipo declarado. Mudar um status não é tratado como renomeação: haverá um status removido e outro adicionado. Mudar um nome de campo também produz remoção e adição.

As regras verificam somente esse modelo. Uma comparação compatível não comprova comportamento real, compatibilidade de clientes específicos, disponibilidade ou segurança da API.

## Saída e automação

```json
{
  "changes": [
    {
      "code": "required_changed",
      "location": "/request/fields/category/required",
      "method": "GET",
      "path": "/catalog",
      "severity": "breaking"
    }
  ],
  "compatible": false,
  "profile_version": 1,
  "summary": {"breaking": 1, "compatible": 0}
}
```

O trecho mostra uma mudança isolada; o exemplo `breaking.json` contém quatro. A ordem é estável por caminho, método e localização. Valores de `enum` não são repetidos no relatório; nomes de operações e campos são apresentados. Não use nomes confidenciais em relatórios destinados à publicação.

| Código de saída | Significado |
| --- | --- |
| `0` | Comparação válida, sem mudanças incompatíveis |
| `1` | Comparação válida, com mudanças incompatíveis |
| `2` | Entrada inválida, argumento incorreto ou erro de leitura; nenhum diff em stdout |

A CLI escreve o relatório em stdout. Você pode redirecioná-lo para um arquivo novo. O programa não cria, modifica ou sobrescreve arquivos por conta própria; a política de redirecionamento pertence ao shell.

## Usar como biblioteca

```python
from http_contract_diff import compare_contracts, load_contract

old = load_contract("examples/before.json")
new = load_contract("examples/compatible.json")
report = compare_contracts(old, new)
assert report["compatible"]
```

`validate_contract(document)` também aceita um `dict` Python e produz um snapshot imutável. Para detectar chaves repetidas no JSON de entrada, use `load_contract`: um `dict` já decodificado não conserva duplicatas.

## Limites e validação

- Até **1 MiB por arquivo**, lido com limite antes do parsing.
- Até **500 operações**, **10.000 campos no documento** e **128 valores por enum**.
- UTF-8, chaves JSON únicas, números finitos e tipos explícitos. Booleanos não são números; `1.0` não é um `integer`. Em `number`, `1` e `1.0` representam o mesmo valor e são duplicatas.
- Nomes têm limite de tamanho e não podem conter controles ou substitutos Unicode isolados.
- Nenhuma busca de `$ref`, plugin, execução de código ou conexão com servidor.
- A entrada deve ser um arquivo local comum. Leitura de dispositivos, pipes, arquivos especiais e streams sem fim não integra o contrato de uso.

## Testes

```bash
python -m unittest discover -s tests -v
```

A suíte verifica direção request/response com um oráculo independente de conjuntos finitos, snapshots, duplicatas, Unicode, limites de arquivo, determinismo e códigos de saída da CLI. O CI executa os mesmos testes em Python 3.11, 3.12 e 3.13.

## Contribuir e licença

Veja [CONTRIBUTING.md](CONTRIBUTING.md), [SECURITY.md](SECURITY.md) e [CHANGELOG.md](CHANGELOG.md). Licença [MIT](LICENSE).

Projeto aberto da **[LOFRANO TECNOLOGIA](https://lofranotecnologia.com.br)** — software sob medida, inteligência artificial, automação e integrações.
