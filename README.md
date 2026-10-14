# Meu Dinheiro

Aplicação local de finanças em Python, com tela no navegador e API JSON. Organiza lançamentos pessoais e de empresa, importa extratos e faturas, mostra gráficos mensais e permite revisar sugestões de categorias para compras.

## Começar

Para o primeiro acesso, requer macOS com Touch ID cadastrado, as ferramentas de linha de comando do Xcode (`xcrun swiftc`) e Python 3.10 ou mais recente. A biblioteca `pypdf` já está incluída em `vendor`. O login com ChatGPT usa `PyJWT` e `cryptography` para validar a identidade.

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python app.py
```

Acesse <http://127.0.0.1:8765>. Para usar outra porta ou outro banco SQLite:

```bash
.venv/bin/python app.py --port 8767 --db /caminho/para/financas.db
```

O banco padrão fica em `data/financas.db` e é criado na primeira execução. **Bancos, extratos, faturas, senhas e chaves de API não fazem parte do repositório.** Faça backup do banco separadamente. O servidor escuta apenas em `127.0.0.1`.

## Acesso local: Touch ID e senha

Ao abrir ou atualizar a página, desbloqueie com **Touch ID** ou com a **senha local do aplicativo**, se já estiver cadastrada. A API financeira exige uma sessão aprovada por uma dessas formas. A sessão dura cinco minutos a partir do login e fica apenas na memória do navegador e do servidor. Minimizar ou trocar de aba preserva o acesso nesse prazo. **Bloquear**, fechar ou atualizar a página exige novo login; a sessão também expira no servidor.

Para cadastrar a primeira senha, entre com Touch ID e abra **Configurações → Acesso ao aplicativo**. Use pelo menos 12 caracteres e confirme a nova senha. Ao entrar por senha, informe a senha atual para alterá-la ou removê-la. Uma sessão aprovada por Touch ID pode definir uma nova senha sem a anterior. Não há recuperação por e-mail ou senha do macOS. A remoção fica bloqueada quando o Touch ID está indisponível, para evitar ficar sem acesso.

A senha nunca é gravada em texto: o arquivo privado `data/local-identity.json`, ignorado pelo Git e com permissão `0600`, armazena um hash scrypt (N=32768, r=8, p=1, salt aleatório de 32 bytes), além da identidade local estável. Comparação em tempo constante, tentativas serializadas e espera progressiva a partir de cinco erros reduzem tentativas repetidas; o limite fica na memória deste servidor local e reiniciar o processo o reinicia. Trocar/remover a senha revoga as outras sessões. A identidade e o método de login acompanham cada sessão, preparando a evolução futura. Existe apenas um usuário atual; não há CRUD de usuários, isolamento entre usuários nem contextos fiscais novos nesta versão.

O aplicativo usa LocalAuthentication da Apple e não recebe nem armazena sua digital. Se o Touch ID ficar indisponível, uma senha previamente cadastrada permite continuar acessando. Sem senha cadastrada, o primeiro acesso ainda requer Touch ID.

O login local protege a **interface e a API local** contra acesso casual com o Mac já desbloqueado. O banco SQLite e as credenciais locais continuam em arquivos no disco, sem criptografia própria; um usuário com acesso à sua sessão do macOS ou um programa malicioso executado nela pode ler esses arquivos diretamente. Para proteger os dados se o Mac for perdido ou roubado, mantenha **FileVault**, senha forte e bloqueio automático do macOS ativados. O servidor local não deve ser exposto à rede.

## Ocultar valores

O botão de olho no topo alterna **Ocultar valores / Mostrar valores** em todas as abas. Esconde quantias, percentuais, quantidades de cotas, dicas dos gráficos e valores digitados nos campos monetários. Os gráficos e o relatório semanal também ficam ocultos para evitar revelar números pelo desenho ou pelo texto livre. A preferência é lembrada neste navegador; atualizações novas continuam ocultas. Ao mostrar novamente, a tela recupera os valores mais recentes sem consultar a API de novo. O botão muda somente a exibição; os registros e cálculos permanecem no aplicativo. O login local continua controlando o acesso.

Na aba Investimentos, **O que seu dinheiro rendeu** destaca ganho/perda em reais e rentabilidade estimada no histórico disponível. O período e a origem aparecem no mesmo quadro; ambos acompanham o histórico escolhido em **Seu rendimento contra o CDI**. Aportes e saques externos são ajustados pelo cálculo existente. CDI ausente não bloqueia o resultado da carteira. Histórico insuficiente ou movimentações pendentes de revisão aparecem como indisponíveis, sem preencher zero.

## Funcionalidades

- Cadastro, edição e exclusão de receitas e despesas, com valores armazenados em centavos inteiros.
- Aba **Planos** para criar e revisar uma reserva de emergência Pessoal ou PJ, simular aportes e acompanhar a meta.
- Visões **Pessoal**, **PJ** e **Tudo**. Transferências identificadas entre contas próprias não duplicam a receita consolidada.
- Importação de faturas PDF de cartão e extratos CSV/OFX, com prévia e proteção contra importação repetida.
- Importação de extratos CSV da XP com prévia e conciliação de PIX enviados pelo Inter; o histórico da XP fica separado do fluxo de caixa e dos gráficos de gastos.
- Aba **Investimentos** com carteira atual, composição por classe, variação bruta por ativo, aportes por mês, aportes acumulados e movimentações da XP, atualizada dinamicamente pelos arquivos importados.
- Estimativa de rendimento dos ativos da posição XP: cotas de fundos pela CVM e ações/FIIs pela brapi, consultadas automaticamente a cada três horas enquanto o servidor estiver aberto.
- Aba **Notícias** com pesquisa sob demanda dos ativos da XP por GPT-6 Luna, fontes clicáveis, cenários de curto, médio e longo prazo e oportunidades para estudar.
- Histórico de faturas por mês, banco e cartão.
- Gráficos de entradas e saídas, faturas, saldos e gastos por categoria em barras horizontais mensais, gerados dinamicamente a partir da API. Barras e pontos mostram os valores ao passar o mouse ou usar o teclado; a legenda do gráfico mensal destaca uma categoria em todos os meses. Meses sem extrato não são apresentados como saldo zero.
- Compras da conta/débito e do cartão de crédito por categoria, com filtros para gerar sugestões opcionais por IA. As sugestões precisam de confirmação antes de serem salvas. Categorias confirmadas são lembradas localmente para compras com a mesma descrição.
- Registro manual de compras no cartão, vinculado ao mês da fatura.
- Conferência de cobertura dos extratos e faturas por mês, orçamento mensal por categoria e calendário de vencimentos recorrentes.
- Comparação estimada da carteira XP com o CDI quando houver pelo menos duas posições datadas.
- Cadastro e edição manual de aportes, resgates e patrimônio total da XP por data, com conciliação de aportes que depois aparecem no CSV.

As **saídas das contas** incluem pagamentos de fatura. Nos gráficos, categorias, orçamentos e lançamentos mensais, compras de cartão usam **o mês da fatura** e débitos usam a data do lançamento. Cada parcela fica na fatura correspondente, mesmo que a data original da compra se repita. A data original é preservada e continua visível no lançamento. Compras não são somadas novamente ao fluxo de caixa. Transferências internas e cashback identificados ficam fora da receita consolidada. Em **Lançamentos do mês**, o filtro por categoria permite conferir e editar grupos como **Outros** sem alterar automaticamente nenhuma classificação.

O CSV de conta corrente do Inter traz data, histórico, descrição, valor e saldo, mas não identifica o banco de destino de um PIX. Uma transferência para o próprio nome pode ir para outra conta pessoal ou para uma corretora; por isso o programa não assume que é investimento. Se o extrato trouxer explicitamente **XP Investimentos** ou **Banco XP**, o débito é marcado como transferência para investimento. Um PIX enviado e devolvido no mesmo dia, com mesmo destinatário e valor, fica fora de receitas e gastos. Para lançamentos sem banco de destino, confira o comprovante e use **É investimento** no histórico do mês; o saldo da conta continua vindo do extrato.

## Importar dados

Na seção **Trazer extrato do banco**, escolha Inter ou Banco do Brasil, o tipo de arquivo e a visão Pessoal ou PJ. Use o mesmo nome de conta ou cartão nas importações seguintes.

Para faturas PDF, é possível selecionar vários arquivos do mesmo banco e cartão. Confirme o mês de cada fatura, veja a prévia e só então importe. O leitor funciona com PDFs que contêm texto selecionável; PDFs digitalizados como imagem ainda precisam de OCR. O formato do Inter é conferido contra o total impresso na fatura quando disponível. **O layout de faturas do BB ainda não foi validado com uma amostra real; confira cuidadosamente a prévia e a soma antes de confirmar.** PDFs protegidos por AES podem exigir o pacote opcional `cryptography` no ambiente Python.

Para extratos CSV/OFX, selecione um arquivo por vez. Em CSV, confira o mapeamento de data, descrição e valor e o sinal das despesas. O arquivo original e a senha não são armazenados; somente os lançamentos importados ficam no SQLite. Os limites são 5 MB por PDF ou 2 MB por CSV/OFX, com até 5.000 lançamentos por arquivo.

Na aba **Investimentos**, envie o CSV da conta XP e confira a prévia antes de confirmar. O app guarda as linhas em uma tabela separada e monta gráficos de aportes mensais e acumulados. PIX recebidos na XP são cruzados com débitos do Inter apenas quando data, valor e nome coincidem de forma única. Os débitos confirmados passam a **Transferência para investimento** e saem dos gastos; o saldo bancário continua vindo do extrato do Inter. Arquivos de períodos sobrepostos podem ser importados sem duplicar linhas. A movimentação interna da XP não entra como nova receita ou despesa.

### Conexões XP e Inter pelo Meu Pluggy

O primeiro passo da integração está na aba **Configurações**, seção **Meu Pluggy**. Para uso pessoal, siga o [guia oficial do Meu Pluggy](https://meu.pluggy.ai/api-guide): conecte a XP por **Open Finance** no Meu Pluggy, escolha o conector **MeuPluggy** no Dashboard e copie o `itemId` da XP, `Client ID` e `Client Secret`. O conector direto antigo da XP está em descontinuação; use o Open Finance equivalente. O plano pessoal documentado pela Pluggy aceita até cinco conexões. Guarde esses dados como segredos; não os envie por chat nem coloque no Git.

Insira os identificadores no app local e clique em **Conferir atualização da XP**. O servidor consulta todas as páginas de contas, investimentos e movimentações da API oficial. Confira as datas e clique em **Sincronizar XP pela Pluggy**: o período mostrado substitui as linhas anteriores da conta XP, incluindo CSVs, e os valores atuais dos ativos substituem a posição do dia. A Pluggy passa a ser a fonte para esse período. Custos de aquisição que não vierem do provedor usam a referência local do mesmo ativo quando a quantidade não mudou; ativos sem referência ficam fora do cálculo de rendimento. Datas das cotas aparecem individualmente. **Proventos a receber não são fornecidos nessa consulta:** ativos mais saldo disponível são mostrados como patrimônio parcial. Cada sincronização guarda essa mesma base no histórico automático para comparação com o CDI, separadamente das posições completas e dos saldos manuais. Fotografias completas de outras datas e histórico de aplicações por ativo permanecem disponíveis. O app não lê senha da XP; o consentimento acontece no Meu Pluggy. As credenciais ficam somente em `data/pluggy-xp.json` com permissão 0600 e nunca são devolvidas pela API.

Para o **Inter pessoal**, adicione o `itemId` dele no mesmo formulário. **Conferir lançamentos do Inter** mostra as datas, o saldo e os registros que serão atualizados. O mês inicial da conexão fica gravado no banco local: os CSVs/PDFs anteriores continuam como fonte do histórico, e a Pluggy atualiza a conta desde o primeiro dia desse mês. No cartão, a recuperação fixa o início após a última compra da fatura importada anterior, podendo incluir compras do fim do mês anterior que pertencem à fatura aberta. As datas de início ficam fixas nas próximas sincronizações, inclusive após virar o mês. Isso evita adicionar novamente as compras antigas com outro mês de cobrança. Pagamentos como `PAGAMENTO ON LINE` são liquidações do cartão, não estornos de compras, e não entram nos gastos do cartão. Na conta corrente, o pagamento continua compondo a saída de dinheiro, sem aparecer novamente no gráfico de compras.

**Importar lançamentos conferidos** substitui somente o período novo mostrado na prévia. Categorias e marcações confirmadas são reaproveitadas pelo ID do provedor ou por data, valor e descrição reconhecida; a memória de categorias também continua disponível. Compras `PENDING` com mês de fatura conhecido são provisórias; pendências da conta e operações futuras ficam fora. A prévia não confirma que a fatura aberta está completa: o valor reflete apenas os itens recebidos. Um lançamento novo do cartão sem mês conhecido impede a sincronização. A conta PJ, outros bancos e a integração XP permanecem separados. Repetir a sincronização mantém uma cópia de cada ID da Pluggy e atualiza alterações ou exclusões no período.

Antes de cada substituição, o app salva uma cópia consistente do SQLite em `data/financas.db.before-pluggy-<data>.bak`, com permissão 0600. A gravação do período é atômica; falhas desfazem a substituição. Datas anteriores à cobertura do provedor são mantidas. A primeira e a última data conferidas podem limitar o primeiro ou o último mês; não se presume cobertura de um mês inteiro quando ela não foi recebida. Uma nova importação CSV/PDF que sobreponha um período controlado pela Pluggy é recusada para evitar duplicação. Bancos, cópias, extratos, caches e credenciais ficam ignorados pelo Git. A atualização ocorre pelos botões de consulta e sincronização, sem chamadas periódicas ao provedor.

A Pluggy é um terceiro: ela recebe e trata os dados autorizados por você, conforme sua [política de privacidade](https://www.pluggy.ai/legal). O banco SQLite importado e as credenciais ficam locais, mas isso não significa que os dados compartilhados no consentimento existam só neste computador. O arquivo de credenciais tem permissão 0600, porém não é criptografado em disco. Remover a conexão **no app** apaga apenas as credenciais locais; para interromper o acesso e pedir a exclusão dos dados na Pluggy, gerencie o consentimento no Meu Pluggy ou no banco e siga os canais descritos na política. Não cole Client Secret, itemId ou extratos em issues/commits do GitHub.

Na mesma aba, **Importar posição detalhada** aceita o arquivo XLSX exportado pela XP. O app valida a soma dos ativos, proventos a receber e saldo contra o patrimônio informado, mostra uma prévia e salva uma fotografia por data. Se você importar outro arquivo da mesma data, essa fotografia será atualizada. A composição usa o valor bruto dos ativos; a diferença em relação ao custo informado pela XP é apenas uma comparação dos ativos da fotografia, não a rentabilidade total. Os aportes e o patrimônio são exibidos separadamente. Para acompanhar a evolução real da carteira, importe posições de outras datas.

O gráfico **Rendimento por ativo** usa `valor bruto − custo` da posição selecionada e mostra essa variação em reais e percentual. O total do gráfico soma apenas os ativos da fotografia; proventos a receber, impostos e taxas não entram. Ele não calcula a rentabilidade histórica da carteira.

O quadro **Seu rendimento contra o CDI** aparece quando houver duas ou mais fotografias da XP em datas distintas. Entre posições, ele estima o retorno da conta pelo [método Dietz modificado](https://rpc.cfainstitute.org/sites/default/files/-/media/documents/article/rf-brief/investment-model-validation.pdf): subtrai aportes e saques externos e pondera cada fluxo pelos dias restantes. PIX/TED recebidos ou enviados são reconhecidos como fluxos externos; transferências ambíguas exigem classificação na tela antes do cálculo. O comparador aplica as taxas diárias do CDI ao mesmo capital inicial e aos mesmos fluxos nas respectivas datas. Os retornos dos intervalos são encadeados no gráfico. O resultado é uma **estimativa da conta XP**, sujeita à qualidade do extrato e das fotografias, não a rentabilidade oficial da corretora. Com uma única posição, o quadro explica que falta outra fotografia. O cache do CDI é ampliado para posições históricas quando necessário.

Em **Aportes e resgates**, escolha um investimento da lista, a data e o valor de cada aplicação ou resgate. O app mantém uma tabela local de ativos, preenchida a partir das posições detalhadas XLSX da XP; o menu mostra os ativos da posição importada mais recente. O histórico de um ativo continua identificado mesmo depois que ele sai da posição atual. Você pode editar ou excluir cada registro e acompanhar os valores aplicados e resgatados por ativo.

Marque **Dinheiro entrou ou saiu da XP** somente quando a operação também trouxe dinheiro de fora para a conta XP ou retirou dinheiro dela. Aplicações feitas com saldo que já estava na XP ficam apenas no histórico do ativo e não alteram os gráficos de aportes nem o cálculo de rendimento da carteira. Para registrar só uma transferência para a conta XP, deixe **Saldo da XP · sem ativo** selecionado. Um fluxo externo manual que coincida em data, valor e sentido com o CSV da XP é exibido, mas contado uma só vez. Os registros manuais anteriores a esta versão continuam sendo tratados como fluxos externos sem ativo associado e podem ser editados para escolher um ativo.

Em **Saldo total em uma data**, informe o patrimônio total mostrado pela XP naquela data, incluindo ativos, saldo disponível e proventos a receber. O app usa esse número como fotografia no comparador com o CDI; duas datas distintas são necessárias. Você pode editar o valor salvo para uma data ou excluí-lo. Quando existe também um XLSX na mesma data, o saldo manual prevalece somente para o gráfico comparativo e o indicador de patrimônio; a composição detalhada e a pesquisa de ativos continuam baseadas no XLSX. Datas da cota de fundos não equivalem à data da posição da carteira. Os dados manuais ficam apenas no SQLite local.

### Rendimento automático da XP

Em **Seu rendimento contra o CDI**, escolha **Automático · Pluggy**. Cada sincronização confirmada guarda uma observação de ativos + saldo disponível, incluindo a composição e as datas das cotas. Todas as consultas sincronizadas ficam no SQLite; o gráfico usa a última observação de cada dia. A implantação aproveita posições reais da Pluggy já salvas e não cria valores para dias sem consulta. Uma segunda data permite calcular o ganho em reais e o percentual pelo método Dietz modificado, descontando aportes e saques externos e considerando suas datas. Aplicações e resgates internos não são novos aportes. Fluxos manuais coincidentes com os importados são contados uma vez. Movimentações ambíguas ou sem cobertura sincronizada precisam ser conferidas antes do cálculo.

Proventos **recebidos** identificados no extrato são mostrados como parte do ganho; já estão refletidos no saldo, portanto não são somados novamente ao patrimônio. Proventos **a receber** permanecem fora da base automática. Custos de aquisição não são necessários para a rentabilidade entre posições; o quadro **Ganho acumulado por ativo** continua usando o custo de referência para mostrar a diferença da posição. Cotações de datas anteriores à consulta são sinalizadas. O cálculo é uma estimativa dos valores fornecidos, não a rentabilidade oficial da XP.

**Saldos completos / manuais** mantém o histórico informado anteriormente, sem misturá-lo à base automática. Se as taxas CDI estiverem indisponíveis, o app exibe o ganho da carteira e deixa a referência aguardando dados. Sincronizações da XP são iniciadas em Configurações; a consulta de cotações públicas a cada três horas não sincroniza a conta XP. O [receptor hospedado de webhooks](relay/README.md) permite sincronizar também pelos avisos da Pluggy, após configurar a Railway e registrar os eventos em Configurações.

O gráfico separado **CDI acumulado** consulta a série diária 12 do [Banco Central](https://www3.bcb.gov.br/sgspub/consultarvalores/consultarValoresSeries.do?method=consultarSeries&series=12), soma os dias úteis por juros compostos e permite escolher 1, 3, 6 ou 12 meses. A consulta começa ao iniciar o servidor e se repete a cada três horas; os valores públicos ficam em cache no SQLite. O gráfico mostra a data do último dado publicado e sinaliza quando a atualização falha ou está atrasada. É uma referência percentual bruta, sem impostos ou taxas; não representa o rendimento da sua carteira XP.

Na aba **Finanças**, o **Resumo semanal** usa a última semana completa (segunda a domingo) e a anterior, na visão Pessoal/PJ/Tudo selecionada. Inclui compras no cartão e débito pela data da compra; exclui transferências internas, investimentos e pagamentos de fatura já marcados como excluídos. Totais, maior alta e queda por categoria e o valor sem categoria são calculados localmente. Clique em **Gerar insights com Luna** para pedir uma análise qualitativa; nenhuma chamada à IA ocorre automaticamente. São enviados somente totais em centavos por categoria para as duas semanas e as datas do período. Nomes, descrições, lançamentos individuais, saldos, receitas e arquivos não são enviados. O relatório fica em cache no SQLite enquanto os agregados forem os mesmos. Se faltarem extratos, os totais podem estar incompletos.

O quadro **O que falta neste mês?** compara os períodos cobertos pelos extratos de conta conhecidos com o mês selecionado (ou com o dia atual no mês em andamento). Para cartões conhecidos, indica fatura ausente, apenas lançamentos manuais ou itens importados. Uma fatura com itens importados ainda precisa de conferência: o aplicativo não presume que ela está fechada. Um banco ainda não importado não pode ser detectado automaticamente.

Em **Orçamento por categoria**, defina limites mensais recorrentes separados para Pessoal e PJ. O progresso usa as compras na data da operação, incluindo débito e cartão; pagamentos de fatura e transferências não entram. Limites podem ser substituídos ou removidos, sem editar lançamentos.

Em **Vencimentos e saldo previsto**, cadastre entradas e saídas mensais com valor e dia do mês. Para cartões já presentes no histórico, configure o dia de vencimento e se a fatura é paga no próprio mês ou no seguinte; o valor previsto acompanha os lançamentos registrados naquela fatura e é marcado como parcial quando há apenas registros manuais ou mistura de importados e manuais. O calendário mostra 42 dias à frente e calcula um saldo simples a partir do último saldo importado das contas da visão escolhida. Não confirma pagamentos, nem projeta gastos não cadastrados. Evite cadastrar a mesma fatura como recorrência manual e como vencimento de cartão, pois isso duplicaria a previsão.

O quadro **Rendimento estimado atualizado** consulta a [CVM](https://dados.cvm.gov.br/dataset/fi-doc-inf_diario) para as cotas oficiais dos fundos e a [brapi.dev](https://brapi.dev/) para ações e FIIs. Os CNPJs dos fundos ficam no arquivo local `data/market-assets.json`, no formato `{"fund_cnpjs":{"Nome exato do fundo":"00.000.000/0000-00"}}`. Esse arquivo é ignorado pelo Git; o app já reconhece automaticamente nomes de ações e FIIs no formato de ticker. Crie um token na brapi e cole no campo da aba **Configurações**. O token é salvo em `data/brapi-token`, com permissão 0600, e nunca é devolvido pela API. O plano gratuito da brapi pode ter cotação atrasada e exige token para vários tickers. O app consulta cada ticker separadamente. Se trocar ou revogar o token, use o mesmo campo ou **Remover token**.

A primeira consulta começa ao iniciar o servidor; as próximas ocorrem a cada três horas enquanto ele estiver em execução. Uma nova posição ou token dispara outra consulta. As cotações e datas ficam no SQLite; o navegador lê o resultado local e mostra a data de cada fonte. A CVM publica cotas diárias, portanto várias consultas no mesmo dia podem manter o mesmo valor. Para fundos, a estimativa aplica a variação da cota oficial ao valor da fotografia XP, usando uma cota base até sete dias antes da posição. Para ações e FII, multiplica a quantidade da planilha pelo último preço consultado. A estimativa presume que não houve compras, vendas ou resgates após a fotografia. Barras cinzas, marcadas **XP**, mostram a posição importada enquanto não há cotação nova. Se faltarem cotações de qualquer ativo, o total atualizado fica **Parcial**, sem somar valores de datas diferentes como se fossem completos. O cálculo não inclui proventos recebidos, taxas, impostos nem fluxos posteriores; não é a rentabilidade oficial da conta XP. Importe uma nova posição da XP para atualizar a base.

Na aba **Notícias**, clique em **Atualizar pesquisa** para buscar informações atuais dos ativos da posição XP mais recente. A pesquisa não roda automaticamente. Ela usa GPT-6 Luna e busca na web; resultados sem link encontrado na busca são omitidos ou substituídos por uma mensagem neutra. Para fundos com nome extenso, o app também confere se o endereço da fonte corresponde ao produto, evitando trocar fundos da mesma família. Essa verificação não audita todas as afirmações do modelo. O relatório fica no SQLite até ser atualizado; uma nova posição com outra data ou lista de ativos pede uma nova pesquisa. O app envia apenas os nomes de até oito ativos, priorizados por valor. Quando há mais ativos, a tela informa quantos ficaram fora. Revise as fontes, as datas e os riscos antes de tomar decisões. As oportunidades são temas para estudar, não recomendações de compra.

Se a fatura atual ainda não puder ser exportada, use **Adicionar compra à fatura**. Escolha banco, área e o mesmo nome de cartão usado nas faturas anteriores; informe data, valor, descrição, categoria e mês da fatura. A compra aparece no histórico e nos gráficos do cartão, mas não altera o saldo da conta corrente. Se importar o PDF dessa fatura depois, confira e exclua lançamentos repetidos.

## Categorias com IA

Clique em **Continuar com ChatGPT** na aba **Configurações**. O programa abre o login no navegador padrão; entre na sua conta e autorize o uso do plano. O retorno acontece em `http://127.0.0.1:<porta>/auth/callback`, no próprio servidor local, e volta para Configurações. Depois, na aba Finanças, escolha o período e a forma de pagamento (**Conta / débito**, **Cartão de crédito** ou ambos), gere sugestões para até 40 compras, revise e clique em **Aplicar categorias**. A visão Pessoal/PJ/Tudo vale também para as sugestões. Se houver débitos PJ sem categoria enquanto a visão Pessoal estiver aberta, a tela oferece um atalho para a PJ. O botão **Gerenciar uso** abre as configurações do ChatGPT. O uso compartilha os limites do seu plano; não usa a cobrança da API key.

Ao aplicar uma categoria, o app a associa localmente à descrição normalizada do estabelecimento, separadamente para Pessoal e PJ. Escritas manuais e correções feitas na edição de lançamentos também atualizam essa memória; categorias de compras já classificadas antes desta versão são aproveitadas na primeira inicialização. A mesma descrição usa a categoria aprendida nas próximas sugestões, identificada como **Aprendido com você** na prévia, e ainda pode ser corrigida antes de salvar. Descrições genéricas como “Pix” não geram regra. Isso é uma memória do app no SQLite, não um treinamento permanente do modelo.

Na normalização, o app remove o prefixo bancário “Compra no débito” e os campos de cidade/país de descrições com colunas separadas por espaços, preservando o nome completo do estabelecimento. Isso permite reaproveitar uma categoria confirmada quando a Pluggy acrescenta esses campos. Nomes de estabelecimentos diferentes continuam separados. A mesma limpeza também é aplicada às descrições enviadas para sugestões por IA.

O quadro **Compras por categoria** permite alternar entre o mês selecionado e todo o histórico. Depois de aplicar categorias a todo o histórico, a tela passa a mostrar o total histórico nesse quadro. O gráfico mensal em barras horizontais mostra o valor total de cada mês e a participação de cada categoria; segue a visão Pessoal/PJ/Tudo e o período dos gráficos. Clique em uma cor da legenda para destacar aquela categoria nos meses; clique de novo para mostrar todas.

Os gráficos por categoria começam mostrando **somente compras categorizadas**. Marque **Mostrar gastos sem categoria** para incluí-las nos dois gráficos. Os textos informam o valor mostrado, o total de compras detalhadas e o valor que aguarda classificação; o filtro não apaga nem altera os lançamentos. A antiga categoria interna `Importado` é exibida como **Sem categoria**: significa uma compra ainda não classificada, não um total de fatura. A lista mantém a descrição para revisão. Aplicações e resgates de CDB e produtos equivalentes identificados explicitamente no extrato são movimentos de investimento, fora do consumo; o saldo bancário continua vindo do provedor.

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
| GET | `/data-health?month=AAAA-MM&scope=personal` | Cobertura dos extratos e estado das faturas conhecidas |
| GET/POST/DELETE | `/budgets` | Limites por categoria; POST recebe `scope`, `category`, `limit`; DELETE usa `scope` e `category` na query |
| GET/POST | `/calendar` | Vencimentos, recorrências e projeção de 42 dias; POST cria uma recorrência |
| DELETE | `/calendar/{id}` | Remove uma recorrência |
| POST | `/calendar/cards` | Configura vencimento de cartão (`source`, `scope`, `due_day`, `month_offset`) |
| DELETE | `/calendar/cards/{id}` | Remove a configuração de vencimento do cartão |
| GET | `/plans/reserve?scope=personal` | Plano da reserva, progresso, projeção mensal e conferência parcial do caixa |
| POST | `/plans/reserve/preview` | Simula o plano sem salvar |
| POST | `/plans/reserve` | Cria ou atualiza o plano para `personal` ou `business` |
| DELETE | `/plans/reserve?scope=personal` | Exclui somente o plano; preserva lançamentos e investimentos |
| GET | `/plans/history?scope=personal&months=6` | Evidências de gastos, cobertura de 3 ou 6 meses encerrados, recorrências e revisão anterior |
| POST | `/plans/history/preview` | Simula papéis das categorias, renda confirmada, margem e cenários sem salvar |
| POST | `/plans/history/review` | Guarda a revisão confirmada por área, sem alterar o plano nem transações |
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
| GET | `/investments/cdi?months=6` | CDI diário acumulado (1, 3, 6 ou 12 meses), com data e fonte do Banco Central |
| GET | `/investments/performance?basis=auto` | Estimativa Dietz da carteira XP comparada ao CDI sob os mesmos fluxos |
| PUT | `/investments/flows/{import_key}` | Classifica uma transferência XP como `external` ou `internal` após revisão |
| GET | `/investments/manual` | Lista ativos atuais, totais por ativo, histórico de aportes/resgates e saldos manuais |
| POST | `/investments/manual/flows` | Cria aporte ou resgate (`occurred_on`, `kind`, `amount`, `asset_id` opcional, `external` booleano) |
| PUT/DELETE | `/investments/manual/flows/{id}` | Edita ou exclui aporte ou resgate |
| POST | `/investments/manual/snapshots` | Cria ou atualiza saldo total da carteira (`as_of`, `total`) |
| DELETE | `/investments/manual/snapshots/{AAAA-MM-DD}` | Exclui saldo manual daquela data |
| GET | `/investments/pluggy/status` | Informa quais conexões estão configuradas, sem devolver credenciais |
| POST/DELETE | `/investments/pluggy/config` | Salva ou remove Client ID, Client Secret e itemIds locais |
| POST | `/investments/pluggy/preview` | Consulta ativos da XP pela Pluggy para conferência, sem alterar a carteira |
| POST | `/investments/pluggy/sync-preview` | Prévia de posição XP e substituição das movimentações; retorna token temporário |
| POST | `/investments/pluggy/import` | Aplica a posição e substitui o período XP a partir do token revisado |
| POST | `/accounts/pluggy/inter/preview` | Prévia de conta, cartão e datas que substituirão os registros do Inter pessoal |
| POST | `/accounts/pluggy/inter/import` | Substitui o período revisado pelo token temporário, preservando categorias reconhecidas |
| GET | `/pluggy/webhooks/status` | Estado do receptor e últimos avisos, sem devolver chaves |
| POST | `/pluggy/webhooks/setup` | Gera duas chaves locais e os itemIds para configurar a Railway |
| POST/DELETE | `/pluggy/webhooks/config` | Configura o receptor HTTPS ou desconecta o consumidor local |
| POST | `/pluggy/webhooks/register` | Registra os cinco tipos de evento na Pluggy |
| POST | `/pluggy/webhooks/check` | Consome avisos e sincroniza conexões atualizadas |
| GET/POST | `/weekly-summary` | Totais semanais agregados e geração sob demanda de insights com Luna; `scope` aceita `personal`, `business` ou `all` |
| GET | `/investments/market-returns` | Estimativas por ativo, datas e fontes das cotações, estado do token e da consulta |
| POST | `/investments/brapi-token` | Salvar token local da brapi (`{"token":"..."}`) |
| DELETE | `/investments/brapi-token` | Remover token local da brapi |
| GET | `/investments/news` | Ler o último relatório da posição XP atual e o estado da conexão |
| POST | `/investments/news` | Atualizar manualmente a pesquisa dos ativos com GPT-6 Luna e busca web |
| POST | `/investments/positions/preview` | Conferir a posição detalhada XLSX da XP (`filename` e `content_base64`) |
| POST | `/investments/positions` | Salvar ou atualizar a fotografia da carteira na data da planilha |
| GET | `/categorization/status?scope=personal&month=AAAA-MM&payment_type=account` | Estado da categorização e pendências por conta/cartão e Pessoal/PJ |
| GET | `/local-auth/status` | Formas de login disponíveis; não contém identidade nem credenciais |
| POST | `/local-auth/unlock` | Criar sessão mediante Touch ID |
| POST | `/local-auth/password-login` | Criar sessão mediante senha local já cadastrada |
| GET | `/local-auth/session` | Identidade e método da sessão atual; exige login |
| POST | `/local-auth/password` | Cadastrar, alterar ou remover senha local; exige login e mesma origem |
| POST | `/local-auth/lock` | Revogar a sessão atual |
| POST | `/auth/chatgpt/start` | Abrir autorização do ChatGPT |
| GET | `/auth/callback` | Receber o retorno OAuth no servidor local |
| POST | `/auth/chatgpt/disconnect` | Desconectar o ChatGPT |
| POST | `/categorization/suggest` | Sugerir categorias sem salvar; corpo aceita `scope`, `month` e `payment_type` (`all`, `account`, `credit`) |
| POST | `/categorization/apply` | Salvar categorias revisadas |

`scope` aceita `personal` e `business`; leituras de resumo e lista também aceitam `all`. As rotas de importação recebem JSON com `filename` e `content` para CSV/OFX ou `content_base64` para PDF, além de banco, tipo de conta e opções de importação. A tela monta esse JSON.

As rotas da XP recebem `filename` e `content` do CSV no mesmo JSON. O formato esperado tem as colunas `Data;Hora;Descricao;Valor;Saldo`.

## Crie seu plano

### Montar a partir dos gastos

O painel **Um plano que começa com seus gastos** analisa os últimos 3 ou 6 meses encerrados. Havendo pelo menos três meses com extratos completos para as fontes conhecidas e faturas registradas, usa esses meses nas médias. Caso contrário, mostra uma base provisória dos meses com gastos observados. Meses ausentes não são tratados como zero; faturas registradas não comprovam fechamento. Compras pendentes, lançamentos excluídos, pagamentos de fatura, transferências internas e movimentos de investimento identificados ficam fora. Médias de despesas são brutas: estornos sem vínculo com uma compra não são abatidos nem viram renda recorrente. O resumo semanal continua usando a data da compra, para descrever os hábitos de cada semana.

Revise o papel de cada categoria: **Essencial**, **Flexível** ou **A revisar**. As sugestões iniciais são apenas uma base editável. Sua revisão fica separada das categorias das compras. Gastos a revisar continuam no custo dos cenários, mas não entram na meta da reserva. As cobranças recorrentes são evidências (um lançamento por mês, em pelo menos três meses, com variação de até 20% entre menor e maior valor em relação à mediana), sem criar contas fixas ou somar novamente os valores. Possíveis parcelamentos pedem revisão do prazo restante. Faturas atuais e futuras registradas aparecem separadamente como compromissos conhecidos.

Confirme a **renda líquida mensal disponível** e ajuste a margem para imprevistos, inicialmente 10% e editável entre 0% e 50%. Entradas do histórico nunca preenchem automaticamente a renda; há um botão para copiá-las para revisão. Transferências PJ/PF não são renda nova. Três cenários comparam manter os gastos, reduzir 10% dos flexíveis ou reduzir 20%. Aporte no cenário é `máximo(0, renda confirmada − média dos gastos + redução dos flexíveis − margem)`. Os cortes são hipóteses, e despesas ausentes ou novos compromissos podem reduzir a sobra real. Não há recomendação automática de ativo nem previsão de rentabilidade.

Marque a confirmação da revisão para **Usar essenciais no plano** ou **Usar base e este aporte**. Isso guarda suas preferências localmente e preenche o rascunho da reserva. Ainda é necessário salvar o plano abaixo. Uma mudança no histórico invalida a revisão antiga; atualize a análise antes de aplicar. Nenhum dado sai deste Mac e nenhuma chamada à IA é feita nesta análise.

### Meta e acompanhamento

Abra **Planos**, escolha Pessoal ou PJ e preencha despesas essenciais mensais, meses de proteção, aporte mensal, saldo já reservado, data de confirmação do saldo e próximo aporte. **Simular** calcula sem salvar; **Criar meu plano / Salvar ajustes** guarda um plano por área no SQLite local. Atualize o saldo manualmente quando ele mudar. O dinheiro reservado é confirmado por você: o app não atribui automaticamente o patrimônio da XP ou os saldos bancários à reserva.

A meta é `despesas essenciais × meses de proteção`. A previsão conta os aportes necessários para cobrir o restante e reduz o último aporte ao valor que falta. Ela preserva o dia mensal, ajustando meses menores ao último dia, e mostra até 24 aportes no gráfico. Não presume rentabilidade, inflação, taxas ou novos resgates. Zero de aporte pausa a previsão; alcançar a meta encerra os aportes projetados. As simulações e o saldo informado são apresentados separadamente. Salvar ou excluir o plano não cria transferências, lançamentos nem ordens de investimento.

A **Conferência de caixa** mostra um cenário parcial das próximas seis semanas: saldo de contas da área, vencimentos cadastrados e os novos aportes propostos. Pode alertar para saldo negativo, base desatualizada, ausência de saldo ou faturas parciais. Não inclui despesas não cadastradas, não confirma receitas e não identifica automaticamente dinheiro já reservado dentro das contas; saldo positivo não comprova capacidade de aporte. Revise a agenda antes de usar a simulação.

### Ideia para depois

Previsão de gastos por métodos estatísticos ou aprendizagem de máquina, após validar cobertura, classificação e quantidade de histórico. O plano atual usa cálculos determinísticos.

## Desenvolvimento

```bash
.venv/bin/python -m pip install -r requirements-dev.txt
.venv/bin/python -m unittest discover -s tests -v
node tests/test_browser_scripts.js
node --check static/app.js
node --check static/import.js
node --check static/charts.js
node --check static/categories.js
node --check static/investments.js
node --check static/news.js
```

Para testar a ocultação de valores no navegador, usando somente números fictícios:

```sh
npm ci
npx playwright install chromium
npm test
```

O teste de Planos inicia um servidor temporário com dados fictícios e autenticação simulada. Não usa o banco real nem o Touch ID do Mac. Defina `PYTHON_BIN=.venv/bin/python` para executá-lo com o ambiente virtual local.

O pacote [`pypdf`](https://pypdf.readthedocs.io/) incluído em `vendor` mantém sua licença em [`vendor/PYPDF-LICENSE.txt`](vendor/PYPDF-LICENSE.txt).
