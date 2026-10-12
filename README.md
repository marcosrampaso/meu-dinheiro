# Meu Dinheiro

Aplicação local de finanças em Python, com tela no navegador e API JSON. Organiza lançamentos pessoais e de empresa, importa extratos e faturas, mostra gráficos mensais e permite revisar sugestões de categorias para compras.

## Começar

Requer Python 3.10 ou mais recente. A biblioteca `pypdf` já está incluída em `vendor`. O login com ChatGPT usa `PyJWT` e `cryptography` para validar a identidade.

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python app.py
```

Acesse <http://127.0.0.1:8765>. Para usar outra porta ou outro banco SQLite:

```bash
.venv/bin/python app.py --port 8767 --db /caminho/para/financas.db
```

O banco padrão fica em `data/financas.db` e é criado na primeira execução. **Bancos, extratos, faturas, senhas e chaves de API não fazem parte do repositório.** Faça backup do banco separadamente. O servidor escuta apenas em `127.0.0.1`; uma futura versão acessível pela rede precisará de autenticação.

## Funcionalidades

- Cadastro, edição e exclusão de receitas e despesas, com valores armazenados em centavos inteiros.
- Visões **Pessoal**, **PJ** e **Tudo**. Transferências identificadas entre contas próprias não duplicam a receita consolidada.
- Importação de faturas PDF de cartão e extratos CSV/OFX, com prévia e proteção contra importação repetida.
- Histórico de faturas por mês, banco e cartão.
- Gráficos de entradas e saídas, faturas, saldos e gastos por categoria em barras horizontais mensais, gerados dinamicamente a partir da API. Barras e pontos mostram os valores ao passar o mouse ou usar o teclado; a legenda do gráfico mensal destaca uma categoria em todos os meses. Meses sem extrato não são apresentados como saldo zero.
- Compras da conta/débito e do cartão de crédito por categoria, com filtros para gerar sugestões opcionais por IA. As sugestões precisam de confirmação antes de serem salvas. Categorias confirmadas são lembradas localmente para compras com a mesma descrição.
- Registro manual de compras no cartão, vinculado ao mês da fatura.

As **saídas das contas** incluem pagamentos de fatura. As compras feitas no cartão aparecem na data da compra e no histórico da fatura, sem serem somadas novamente ao fluxo de caixa. Transferências internas e cashback identificados ficam fora da receita consolidada. Confira as marcações após importar novos formatos de extrato.

## Importar dados

Na seção **Trazer extrato do banco**, escolha Inter ou Banco do Brasil, o tipo de arquivo e a visão Pessoal ou PJ. Use o mesmo nome de conta ou cartão nas importações seguintes.

Para faturas PDF, é possível selecionar vários arquivos do mesmo banco e cartão. Confirme o mês de cada fatura, veja a prévia e só então importe. O leitor funciona com PDFs que contêm texto selecionável; PDFs digitalizados como imagem ainda precisam de OCR. O formato do Inter é conferido contra o total impresso na fatura quando disponível. **O layout de faturas do BB ainda não foi validado com uma amostra real; confira cuidadosamente a prévia e a soma antes de confirmar.** PDFs protegidos por AES podem exigir o pacote opcional `cryptography` no ambiente Python.

Para extratos CSV/OFX, selecione um arquivo por vez. Em CSV, confira o mapeamento de data, descrição e valor e o sinal das despesas. O arquivo original e a senha não são armazenados; somente os lançamentos importados ficam no SQLite. Os limites são 5 MB por PDF ou 2 MB por CSV/OFX, com até 5.000 lançamentos por arquivo.

Se a fatura atual ainda não puder ser exportada, use **Adicionar compra à fatura**. Escolha banco, área e o mesmo nome de cartão usado nas faturas anteriores; informe data, valor, descrição, categoria e mês da fatura. A compra aparece no histórico e nos gráficos do cartão, mas não altera o saldo da conta corrente. Se importar o PDF dessa fatura depois, confira e exclua lançamentos repetidos.

## Categorias com IA

Clique em **Continuar com ChatGPT** na seção de categorias. O programa abre o login no navegador padrão; entre na sua conta e autorize o uso do plano. O retorno acontece em `http://127.0.0.1:<porta>/auth/callback`, no próprio servidor local. Depois, escolha o período e a forma de pagamento (**Conta / débito**, **Cartão de crédito** ou ambos), gere sugestões para até 40 compras, revise e clique em **Aplicar categorias**. A visão Pessoal/PJ/Tudo vale também para as sugestões. Se houver débitos PJ sem categoria enquanto a visão Pessoal estiver aberta, a tela oferece um atalho para a PJ. O botão **Gerenciar uso** abre as configurações do ChatGPT. O uso compartilha os limites do seu plano; não usa a cobrança da API key.

Ao aplicar uma categoria, o app a associa localmente à descrição normalizada do estabelecimento, separadamente para Pessoal e PJ. Escritas manuais e correções feitas na edição de lançamentos também atualizam essa memória; categorias de compras já classificadas antes desta versão são aproveitadas na primeira inicialização. A mesma descrição usa a categoria aprendida nas próximas sugestões, identificada como **Aprendido com você** na prévia, e ainda pode ser corrigida antes de salvar. Descrições genéricas como “Pix” não geram regra. Isso é uma memória do app no SQLite, não um treinamento permanente do modelo.

O quadro **Compras por categoria** permite alternar entre o mês selecionado e todo o histórico. Depois de aplicar categorias a todo o histórico, a tela passa a mostrar o total histórico nesse quadro. O gráfico mensal em barras horizontais mostra o valor total de cada mês e a participação de cada categoria; segue a visão Pessoal/PJ/Tudo e o período dos gráficos. Clique em uma cor da legenda para destacar aquela categoria nos meses; clique de novo para mostrar todas.

As credenciais OAuth ficam somente em `data/chatgpt-auth.json`, com permissão de leitura restrita, e a pasta `data/` é ignorada pelo Git. O aplicativo renova o token quando necessário e usa apenas um modelo autorizado para a conta conectada. Ao desconectar, ele revoga a sessão renovável quando a rede permite e apaga os tokens locais. O arquivo `.env` de versões anteriores continua ignorado pelo Git, mas **não é mais lido pelo programa**.

A integração usa a API Responses com `store: false` e `stream: true`. Envia apenas IDs e descrições tratadas das compras que ainda não têm categoria aprendida; identificadores comuns nas descrições são mascarados. Valores, datas, saldos, categorias confirmadas e arquivos originais não são enviados. A conexão não dá acesso às suas conversas do ChatGPT. A categorização nunca troca automaticamente para cobrança por API key.

## API

Base: `http://127.0.0.1:8765/api/v1`. Nas escritas, envie `Content-Type: application/json`. Valores monetários de entrada são strings, como `"123,45"`; leituras retornam `amount_cents` como inteiro.

| Método | Rota | Função |
| --- | --- | --- |
| GET | `/summary?month=AAAA-MM&scope=personal` | Resumo mensal |
| GET | `/purchase-categories?scope=personal&month=AAAA-MM` | Gastos por categoria no mês; sem `month`, todo o histórico |
| GET | `/analytics?month=AAAA-MM&scope=all&months=6` | Séries dos gráficos, incluindo `category_spending` por mês; `months` aceita de 1 a 24 |
| GET | `/transactions?month=AAAA-MM&scope=business` | Lista de lançamentos |
| GET | `/transactions/{id}` | Um lançamento |
| POST | `/transactions` | Criar lançamento |
| POST | `/card-purchases` | Registrar compra manual na fatura |
| PUT | `/transactions/{id}` | Atualizar lançamento |
| DELETE | `/transactions/{id}` | Excluir lançamento |
| GET | `/invoices?scope=personal` | Faturas importadas |
| GET | `/invoices/{AAAA-MM}?source=...&scope=personal` | Itens de uma fatura |
| POST | `/imports/inspect` | Identificar formato e colunas |
| POST | `/imports/preview` | Validar lançamentos antes de salvar |
| POST | `/imports` | Importar sem duplicar |
| GET | `/categorization/status?scope=personal&month=AAAA-MM&payment_type=account` | Estado da categorização e pendências por conta/cartão e Pessoal/PJ |
| POST | `/auth/chatgpt/start` | Abrir autorização do ChatGPT |
| GET | `/auth/callback` | Receber o retorno OAuth no servidor local |
| POST | `/auth/chatgpt/disconnect` | Desconectar o ChatGPT |
| POST | `/categorization/suggest` | Sugerir categorias sem salvar; corpo aceita `scope`, `month` e `payment_type` (`all`, `account`, `credit`) |
| POST | `/categorization/apply` | Salvar categorias revisadas |

`scope` aceita `personal` e `business`; leituras de resumo e lista também aceitam `all`. As rotas de importação recebem JSON com `filename` e `content` para CSV/OFX ou `content_base64` para PDF, além de banco, tipo de conta e opções de importação. A tela monta esse JSON.

## Desenvolvimento

```bash
.venv/bin/python -m unittest discover -s tests -v
node --check static/app.js
node --check static/import.js
node --check static/charts.js
node --check static/categories.js
```

O pacote [`pypdf`](https://pypdf.readthedocs.io/) incluído em `vendor` mantém sua licença em [`vendor/PYPDF-LICENSE.txt`](vendor/PYPDF-LICENSE.txt).
