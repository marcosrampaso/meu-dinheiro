"""Personal totals for research. No transactions, identities or business records."""

from datetime import date

from finance import PURCHASE_CATEGORIES
from plan_insights import history_insights
from planning import calendar_overview


def financial_context(store, today=None):
    today = today or date.today()
    history = history_insights(store, 'personal', 6, today)
    calendar = calendar_overview(store, 'personal', today)
    position = store.latest_investment_position()
    with store._connect() as connection:
        saved = connection.execute("SELECT * FROM reserve_plans WHERE scope='personal'").fetchone()
    reserve = ({key:saved[key] for key in ('essential_cents','target_months','monthly_cents',
                                         'reserved_cents','balance_as_of')} if saved else None)
    if reserve:
        reserve['target_cents'] = reserve['essential_cents'] * reserve['target_months']
        reserve['remaining_cents'] = max(0, reserve['target_cents'] - reserve['reserved_cents'])
    categories = {}
    flexible = 0
    for row in history['categories']:
        label = row['category'] if row['category'] in PURCHASE_CATEGORIES else 'Outras categorias'
        categories[label] = categories.get(label, 0) + row['average_cents']
        if row['role'] == 'flexible':
            flexible += row['average_cents']
    assets = []
    total = sum(row['value_cents'] for row in position['assets']) if position else 0
    for row in (position['assets'] if position else [])[:8]:
        assets.append({'asset':row['name'], 'value_cents':row['value_cents'],
                       'allocation_percent':round(100*row['value_cents']/total,2) if total else 0,
                       'cost_cents':row['cost_cents'] if row['cost_known'] else None,
                       'gain_cents':row['value_cents']-row['cost_cents'] if row['cost_known'] else None})
    warnings = list(history['warnings'])
    if calendar['balance_stale']:
        warnings.append('O saldo de pelo menos uma conta tem mais de sete dias.')
    if not reserve:
        warnings.append('Reserva não cadastrada: isso não comprova que a pessoa tenha ou não uma reserva.')
    if reserve and (today-date.fromisoformat(reserve['balance_as_of'])).days > 30:
        warnings.append('O valor confirmado da reserva tem mais de trinta dias.')
    warnings.append('Saldo em conta não é dinheiro livre para investir. Projeção inclui somente compromissos cadastrados; pode faltar fatura ou despesa.')
    return {'as_of':today.isoformat(),'scope':'personal',
            'cash':{'balance_cents':calendar['opening_balance_cents'],
                    'oldest_balance_on':calendar['opening_balance_as_of'],
                    'stale':calendar['balance_stale'],
                    'known_expenses_cents':sum(row['amount_cents'] for row in calendar['events'] if row['kind']=='expense'),
                    'through':calendar['through'], 'investable_cents':None},
            'portfolio':{'as_of':position['as_of'] if position else None,
                         'value_cents':total,'assets':assets,'more_assets':max(0,len(position['assets'])-8) if position else 0,
                         'note':'Valores da última posição importada; ganho sobre custo conhecido não é rentabilidade anual nem inclui necessariamente proventos.'},
            'spending':{'months':history['base_months'],'quality':history['quality'],
                        'average_cents':history['average_spending_cents'],
                        'categories':[{'category':name,'average_cents':amount} for name,amount in
                                      sorted(categories.items(),key=lambda pair:-pair[1])],
                        'confirmed_income_cents':history['preferences']['income_cents'],
                        'saving_scenario':{'reduction_percent':10,'monthly_cents':round(flexible*.1),
                                           'annual_cents':round(flexible*.1)*12,
                                           'note':'Hipótese de corte de 10% das categorias flexíveis; sem rendimento financeiro. Papéis sugeridos precisam de revisão.'}},
            'reserve':reserve,'risk_profile':'not_recorded','debts':'not_recorded','warnings':warnings}
