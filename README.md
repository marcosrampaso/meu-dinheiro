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
- Importação de extratos CSV da XP com prévia e conciliação de PIX enviados pelo Inter; o histórico da XP fica separado do fluxo de caixa e dos gráficos de gastos.
- Aba **Investimentos** com carteira atual, composição por classe, variação bruta por ativo, aportes por mês, aportes acumulados e movimentações da XP, atualizada dinamicamente pelos arquivos importados.
- Estimativa de rendimento de todos os ativos da posição XP: cotas de fundos pela CVM e ABCD11/EFGH11 pela brapi, consultadas automaticamente a cada três horas enquanto o servidor estiver aberto.
- Aba **Notícias** com pesquisa sob demanda dos ativos da XP por GPT-6 Luna, fontes clicáveis, cenários de curto, médio e longo prazo e oportunidades para estudar.
- Histórico de faturas por mês, banco e cartão.
- Gráficos de entradas e saídas, faturas, saldos e gastos por categoria em barras horizontais mensais, gerados dinamicamente a partir da API. Barras e pontos mostram os valores ao passar o mouse ou usar o teclado; a legenda do gráfico mensal destaca uma categoria em todos os meses. Meses sem extrato não são apresentados como saldo zero.
- Compras da conta/débito e do cartão de crédito por categoria, com filtros para gerar sugestões opcionais por IA. As sugestões precisam de confirmação antes de serem salvas. Categorias confirmadas são lembradas localmente para compras com a mesma descrição.
- Registro manual de compras no cartão, vinculado ao mês da fatura.

As **saídas das contas** incluem pagamentos de fatura. As compras feitas no cartão aparecem na data da compra e no histórico da fatura, sem serem somadas novamente ao fluxo de caixa. Transferências internas e cashback identificados ficam fora da receita consolidada. Confira as marcações após importar novos formatos de extrato.

O CSV de conta corrente do Inter traz data, histórico, descrição, valor e saldo, mas não identifica o banco de destino de um PIX. Uma transferência para o próprio nome pode ir para outra conta pessoal ou para uma corretora; por isso o programa não assume que é investimento. Se o extrato trouxer explicitamente **XP Investimentos** ou **Banco XP**, o débito é marcado como transferência para investimento. Um PIX enviado e devolvido no mesmo dia, com mesmo destinatário e valor, fica fora de receitas e gastos. Para lançamentos sem banco de destino, confira o comprovante e use **É investimento** no histórico do mês; o saldo da conta continua vindo do extrato.

## Importar dados

Na seção **Trazer extrato do banco**, escolha Inter ou Banco do Brasil, o tipo de arquivo e a visão Pessoal ou PJ. Use o mesmo nome de conta ou cartão nas importações seguintes.

Para faturas PDF, é possível selecionar vários arquivos do mesmo banco e cartão. Confirme o mês de cada fatura, veja a prévia e só então importe. O leitor funciona com PDFs que contêm texto selecionável; PDFs digitalizados como imagem ainda precisam de OCR. O formato do Inter é conferido contra o total impresso na fatura quando disponível. **O layout de faturas do BB ainda não foi validado com uma amostra real; confira cuidadosamente a prévia e a soma antes de confirmar.** PDFs protegidos por AES podem exigir o pacote opcional `cryptography` no ambiente Python.

Para extratos CSV/OFX, selecione um arquivo por vez. Em CSV, confira o mapeamento de data, descrição e valor e o sinal das despesas. O arquivo original e a senha não são armazenados; somente os lançamentos importados ficam no SQLite. Os limites são 5 MB por PDF ou 2 MB por CSV/OFX, com até 5.000 lançamentos por arquivo.

Na aba **Investimentos**, envie o CSV da conta XP e confira a prévia antes de confirmar. O app guarda as linhas em uma tabela separada e monta gráficos de aportes mensais e acumulados. PIX recebidos na XP são cruzados com débitos do Inter apenas quando data, valor e nome coincidem de forma única. Os débitos confirmados passam a **Transferência para investimento** e saem dos gastos; o saldo bancário continua vindo do extrato do Inter. Arquivos de períodos sobrepostos podem ser importados sem duplicar linhas. A movimentação interna da XP não entra como nova receita ou despesa.

Na mesma aba, **Importar posição detalhada** aceita o arquivo XLSX exportado pela XP. O app valida a soma dos ativos, proventos a receber e saldo contra o patrimônio informado, mostra uma prévia e salva uma fotografia por data. Se você importar outro arquivo da mesma data, essa fotografia será atualizada. A composição usa o valor bruto dos ativos; a diferença em relação ao custo informado pela XP é apenas uma comparação dos ativos da fotografia, não a rentabilidade total. Os aportes e o patrimônio são exibidos separadamente. Para acompanhar a evolução real da carteira, importe posições de outras datas.

O gráfico **Rendimento por ativo** usa `valor bruto − custo` da posição selecionada e mostra essa variação em reais e percentual. O total do gráfico soma apenas os ativos da fotografia; proventos a receber, impostos e taxas não entram. Ele não calcula a rentabilidade histórica da carteira nem uma série mensal de rendimento. Para isso, serão necessários dados de posições anteriores e de proventos.

O quadro **Rendimento estimado atualizado** consulta a [CVM](https://dados.cvm.gov.br/dataset/fi-doc-inf_diario) para as cotas oficiais dos fundos Alpha (CNPJ 12.345.678/0001-90) e Fundo Beta (CNPJ 98.765.432/0001-10). Para ABCD11 e EFGH11, crie um token em [brapi.dev](https://brapi.dev/) e cole no campo da aba Investimentos. O token é salvo em `data/brapi-token`, com permissão 0600, e nunca é devolvido pela API; `data/` é ignorado pelo Git. O plano gratuito da brapi pode ter cotação atrasada e exige token para esses tickers. O app consulta cada ticker separadamente. Se trocar ou revogar o token, use o mesmo campo ou **Remover token**.

A primeira consulta começa ao iniciar o servidor; as próximas ocorrem a cada três horas enquanto ele estiver em execução. Uma nova posição ou token dispara outra consulta. As cotações e datas ficam no SQLite; o navegador lê o resultado local e mostra a data de cada fonte. A CVM publica cotas diárias, portanto várias consultas no mesmo dia podem manter o mesmo valor. Para fundos, a estimativa aplica a variação da cota oficial ao valor da fotografia XP, usando uma cota base até sete dias antes da posição. Para ações e FII, multiplica a quantidade da planilha pelo último preço consultado. A estimativa presume que não houve compras, vendas ou resgates após a fotografia. Barras cinzas, marcadas **XP**, mostram a posição importada enquanto não há cotação nova. Se faltarem cotações de qualquer ativo, o total atualizado fica **Parcial**, sem somar valores de datas diferentes como se fossem completos. O cálculo não inclui proventos recebidos, taxas, impostos nem fluxos posteriores; não é a rentabilidade oficial da conta XP. Importe uma nova posição da XP para atualizar a base.

Na aba **Notícias**, clique em **Atualizar pesquisa** para buscar informações atuais dos ativos da posição XP mais recente. A pesquisa não roda automaticamente. Ela usa GPT-6 Luna e busca na web; resultados sem link encontrado na busca são omitidos ou substituídos por uma mensagem neutra. Para fundos com nome extenso, o app também confere se o endereço da fonte corresponde ao produto, evitando trocar fundos da mesma família. Essa verificação não audita todas as afirmações do modelo. O relatório fica no SQLite até ser atualizado; uma nova posição com outra data ou lista de ativos pede uma nova pesquisa. O app envia apenas os nomes de até oito ativos, priorizados por valor. Quando há mais ativos, a tela informa quantos ficaram fora. Revise as fontes, as datas e os riscos antes de tomar decisões. As oportunidades são temas para estudar, não recomendações de compra.

Se a fatura atual ainda não puder ser exportada, use **Adicionar compra à fatura**. Escolha banco, área e o mesmo nome de cartão usado nas faturas anteriores; informe data, valor, descrição, categoria e mês da fatura. A compra aparece no histórico e nos gráficos do cartão, mas não altera o saldo da conta corrente. Se importar o PDF dessa fatura depois, confira e exclua lançamentos repetidos.

## Categorias com IA

Clique em **Continuar com ChatGPT** na seção de categorias. O programa abre o login no navegador padrão; entre na sua conta e autorize o uso do plano. O retorno acontece em `http://127.0.0.1:<porta>/auth/callback`, no próprio servidor local. Depois, escolha o período e a forma de pagamento (**Conta / débito**, **Cartão de crédito** ou ambos), gere sugestões para até 40 compras, revise e clique em **Aplicar categorias**. A visão Pessoal/PJ/Tudo vale também para as sugestões. Se houver débitos PJ sem categoria enquanto a visão Pessoal estiver aberta, a tela oferece um atalho para a PJ. O botão **Gerenciar uso** abre as configurações do ChatGPT. O uso compartilha os limites do seu plano; não usa a cobrança da API key.

Ao aplicar uma categoria, o app a associa localmente à descrição normalizada do estabelecimento, separadamente para Pessoal e PJ. Escritas manuais e correções feitas na edição de lançamentos também atualizam essa memória; categorias de compras já classificadas antes desta versão são aproveitadas na primeira inicialização. A mesma descrição usa a categoria aprendida nas próximas sugestões, identificada como **Aprendido com você** na prévia, e ainda pode ser corrigida antes de salvar. Descrições genéricas como “Pix” não geram regra. Isso é uma memória do app no SQLite, não um treinamento permanente do modelo.

O quadro **Compras por categoria** permite alternar entre o mês selecionado e todo o histórico. Depois de aplicar categorias a todo o histórico, a tela passa a mostrar o total histórico nesse quadro. O gráfico mensal em barras horizontais mostra o valor total de cada mês e a participação de cada categoria; segue a visão Pessoal/PJ/Tudo e o período dos gráficos. Clique em uma cor da legenda para destacar aquela categoria nos meses; clique de novo para mostrar todas.

As credenciais OAuth ficam somente em `data/chatgpt-auth.json`, com permissão de leitura restrita, e a pasta `data/` é ignorada pelo Git. O aplicativo renova o token quando necessário e usa apenas um modelo autorizado para a conta conectada. Ao desconectar, ele revoga a sessão renovável quando a rede permite e apaga os tokens locais. O arquivo `.env` de versões anteriores continua ignorado pelo Git, mas **não é mais lido pelo programa**.

A integração usa a API Responses com `store: false` e `stream: true`. Envia apenas IDs e descrições tratadas das compras que ainda não têm categoria aprendida; identificadores comuns nas descrições são mascarados. Valores, datas, saldos, categorias confirmadas e arquivos originais não são enviados. A conexão não dá acesso às suas conversas do ChatGPT. A categorização nunca troca automaticamente para cobrança por API key.

A pesquisa de investimentos usa a mesma conexão ChatGPT, mas fixa o modelo `gpt-6-luna` e exige busca na web. Os nomes dos ativos são enviados à OpenAI e as páginas consultadas são processadas pelo modelo; valores, quantidades, números de conta e documentos não são enviados. A pesquisa consome uso do plano e pode estar sujeita a limites próprios da busca web. A disponibilidade da ferramenta depende do acesso da conta conectada. Se a conta não autorizar a busca, o app mostra o erro e mantém o último relatório salvo.

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
| POST | `/investments/imports/preview` | Conferir linhas do CSV da XP e pares com débitos do Inter |
| POST | `/investments/imports` | Salvar o histórico da XP sem duplicar e marcar os pares confirmados |
| GET | `/investments/overview?month=AAAA-MM&months=6` | Aportes mensais e acumulados, conciliações e movimentações para a aba Investimentos |
| GET | `/investments/market-returns` | Estimativas por ativo, datas e fontes das cotações, estado do token e da consulta |
| POST | `/investments/brapi-token` | Salvar token local da brapi (`{"token":"..."}`) |
| DELETE | `/investments/brapi-token` | Remover token local da brapi |
| GET | `/investments/news` | Ler o último relatório da posição XP atual e o estado da conexão |
| POST | `/investments/news` | Atualizar manualmente a pesquisa dos ativos com GPT-6 Luna e busca web |
| POST | `/investments/positions/preview` | Conferir a posição detalhada XLSX da XP (`filename` e `content_base64`) |
| POST | `/investments/positions` | Salvar ou atualizar a fotografia da carteira na data da planilha |
| GET | `/categorization/status?scope=personal&month=AAAA-MM&payment_type=account` | Estado da categorização e pendências por conta/cartão e Pessoal/PJ |
| POST | `/auth/chatgpt/start` | Abrir autorização do ChatGPT |
| GET | `/auth/callback` | Receber o retorno OAuth no servidor local |
| POST | `/auth/chatgpt/disconnect` | Desconectar o ChatGPT |
| POST | `/categorization/suggest` | Sugerir categorias sem salvar; corpo aceita `scope`, `month` e `payment_type` (`all`, `account`, `credit`) |
| POST | `/categorization/apply` | Salvar categorias revisadas |

`scope` aceita `personal` e `business`; leituras de resumo e lista também aceitam `all`. As rotas de importação recebem JSON com `filename` e `content` para CSV/OFX ou `content_base64` para PDF, além de banco, tipo de conta e opções de importação. A tela monta esse JSON.

As rotas da XP recebem `filename` e `content` do CSV no mesmo JSON. O formato esperado tem as colunas `Data;Hora;Descricao;Valor;Saldo`.

## Desenvolvimento

```bash
.venv/bin/python -m unittest discover -s tests -v
node --check static/app.js
node --check static/import.js
node --check static/charts.js
node --check static/categories.js
node --check static/investments.js
node --check static/news.js
```

O pacote [`pypdf`](https://pypdf.readthedocs.io/) incluído em `vendor` mantém sua licença em [`vendor/PYPDF-LICENSE.txt`](vendor/PYPDF-LICENSE.txt).
