"""Deterministic, fictional financial history. Never reads an existing user's files."""
import calendar
from datetime import date, timedelta

from finance import FinanceStore
from plan_insights import _months
from reserve_plan import save_reserve


def seed_demo(store: FinanceStore, today=None):
    today = today or date.today()
    months = _months(today,6)+[today.strftime('%Y-%m')]
    for index,month in enumerate(months):
        end = min(today,date(int(month[:4]),int(month[5:]),calendar.monthrange(int(month[:4]),int(month[5:]))[1]))
        store.save_account_statement('Conta exemplo','personal',{'starts_on':month+'-01','ends_on':end.isoformat()},[])
        income=store.add({'kind':'income','description':'Receita de exemplo','category':'Trabalho','amount':'6500',
                   'occurred_on':month+'-01','scope':'personal'})
        with store._connect() as connection: connection.execute("UPDATE transactions SET source='Conta exemplo' WHERE id=?",(income['id'],))
        for description,category,amount,day,card in [('Moradia exemplo','Moradia',1500,2,False),
            ('Mercado exemplo','Mercado',420+index*13,3,True),('Restaurante exemplo','Alimentação',180+index*9,4,True),
            ('Mobilidade exemplo','Transporte',230,5,False),('Cinema exemplo','Lazer',80+index*5,6,True),
            ('Assinatura exemplo','Assinaturas',49,7,True),('Loja a classificar','Importado',75,8,True)]:
            if day>end.day: continue
            if card:
                store.add_card_purchase({'bank':'inter','account':'cartão exemplo','invoice_month':month,
                    'occurred_on':f'{month}-{day:02}','description':description,'category':category,'amount':str(amount)})
            else:
                cash=store.add({'kind':'expense','description':description,'category':category,'amount':str(amount),
                    'occurred_on':f'{month}-{day:02}','scope':'personal'})
                with store._connect() as connection: connection.execute("UPDATE transactions SET source='Conta exemplo' WHERE id=?",(cash['id'],))
        store.save_invoice_coverage('Inter · cartão exemplo','personal',month)
        store.save_account_balance('Conta exemplo','personal',end.isoformat(),220000+index*17000)
        store.import_xp_positions({'as_of':end.isoformat(),'total_cents':400000+index*25000,
            'assets_cents':400000+index*25000,'receivables_cents':0,'cash_cents':0,'assets':[
                {'name':'Fundo de liquidez exemplo','category':'Fundos de Investimentos',
                 'value_cents':280000+index*16000,'cost_cents':275000+index*15000,'quantity':'1','net_cents':None},
                {'name':'ABCD11','category':'Ações','value_cents':120000+index*9000,
                 'cost_cents':118000+index*8000,'quantity':'10','net_cents':None}]})
    save_reserve(store,{'scope':'personal','name':'Reserva de exemplo','essential_expenses':'2500',
        'target_months':6,'monthly_contribution':'1000','reserved_balance':'3500',
        'balance_as_of':today.isoformat(),'next_contribution_on':(today+timedelta(days=1)).isoformat()},today)
    for use_finances in (False,True):
        context = store.investment_news_context(use_finances)
        report={'uses_finances':use_finances,'schema_version':2,'financial_context':context['financial_context'],
            'assets':[{'asset':asset,'headline':'Exemplo de leitura de um relatório de investimento',
                'summary':'Conteúdo fictício para demonstrar os cards. Não é uma notícia de mercado.',
                'source_url':'https://www.gov.br/investidor/','published_on':'','source_kind':'referencia',
                'what_changed':'Nesta demonstração, o patrimônio cresce com aportes e variações fictícias.',
                'why_it_matters':'Separar aportes de rendimento ajuda a entender a evolução da carteira.',
                'watch_next':'Confira o custo, o período de comparação e as movimentações antes de interpretar o ganho.',
                'short_term':'Exemplo: conferir a liquidez necessária.','medium_term':'Exemplo: acompanhar a formação da reserva.',
                'long_term':'Exemplo: rever a alocação conforme os objetivos.','risk':'Valores e ativos fictícios; não representam uma recomendação.'} for asset in context['assets']],
            'opportunities':[], 'personalization':None}
        if use_finances:
            report['personalization']={'summary':'Exemplo de prioridades baseadas nos dados fictícios.',
                'priorities':[{'title':'Construir a reserva com regularidade','action':'Revise o plano e simule o aporte mensal.',
                    'reason':'A reserva fictícia tem R$ 3.500,00 para uma meta de R$ 15.000,00.',
                    'benefit':'Observar como o aporte altera o prazo.','condition':'Confirme gastos e caixa antes de aplicar.',
                    'risk':'Esta é uma simulação, sem retorno garantido.','basis':'reserve','source_url':''}]}
        store.save_investment_news(context,report,today.isoformat()+'T12:00:00Z','Demonstração local · sem IA')
