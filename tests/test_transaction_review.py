from datetime import date
from pathlib import Path
import tempfile
import unittest

from finance import FinanceStore, ValidationError
from planning import calendar_overview, save_commitment
from plan_insights import history_insights
from transaction_review import review_overview, apply_review


class ReviewTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(); self.addCleanup(self.directory.cleanup)
        self.store = FinanceStore(Path(self.directory.name)/'fixture.db')

    def add(self, description='Posto exemplo', amount='150,00', category='Transporte', scope='personal', **extra):
        return self.store.add({'description':description,'amount':amount,'category':category,'scope':scope,
                               'kind':'expense','occurred_on':'2026-07-10', **extra})

    def correction(self, row, category):
        return {'id':row['id'],'category':category,'signature':row['signature']}

    def test_review_evidence_uses_invoice_month_and_does_not_infer_installments_from_equal_amounts(self):
        for month in ['2026-07','2026-08','2026-09']:
            self.store.add_card_purchase({'bank':'inter','account':'exemplo','scope':'personal',
                'invoice_month':month,'occurred_on':month+'-10','description':'Posto exemplo',
                'amount':'150,00','category':'Transporte'})
        self.store.add_card_purchase({'bank':'inter','account':'exemplo','scope':'personal',
            'invoice_month':'2026-09','occurred_on':'2026-07-10','description':'Loja exemplo Parcela 03 de 03',
            'amount':'100,00','category':'Compras'})
        data=review_overview(self.store,category='Transporte')
        self.assertEqual([row['expense_cents'] for row in data['monthly']],[15000,15000,15000])
        self.assertEqual(data['merchants'][0]['installment_count'],0)
        self.assertTrue(all(not row['installment_hint'] for row in data['items']))
        self.assertEqual(review_overview(self.store,month='2026-09',category='Compras')['expense_cents'],10000)

    def test_filters_and_pagination_keep_scopes_and_protected_movements_separate(self):
        for index in range(55): self.add(description=f'Loja exemplo {index}',category='Outros')
        self.add(scope='business',category='Mercado')
        self.add(category='Pagamento de fatura',excluded=False)
        self.add(kind='income',category='Receita',description='Serviço recebido')
        self.assertEqual(review_overview(self.store)['total'],55)
        self.assertEqual(len(review_overview(self.store)['items']),50)
        self.assertEqual(len(review_overview(self.store,offset=50)['items']),5)
        self.assertEqual(review_overview(self.store,scope='business')['total'],1)
        self.assertEqual(review_overview(self.store,kind='income')['income_cents'],15000)
        expanded=review_overview(self.store,include_excluded=True,category='Pagamento de fatura')
        self.assertFalse(expanded['items'][0]['editable'])
        self.assertEqual(expanded['expense_cents'],0)
        self.assertEqual(review_overview(self.store,search='loja exemplo 54')['total'],1)

    def test_category_save_changes_only_category_and_learns_for_new_standard_purchases(self):
        first=self.add(category='Outros')
        row=review_overview(self.store)['items'][0]
        result=apply_review(self.store,{'items':[self.correction(row,'Compras')],'remember':True})
        self.assertEqual(result,{'applied':1,'learned':1})
        after=self.store.get(first['id'])
        self.assertEqual({**first,'category':'Compras'},after)
        later=self.add(description='POSTO EXEMPLO',category='Importado')
        self.assertEqual(self.store.remembered_categories([later]),{later['id']:'Compras'})

    def test_conflict_scope_and_protected_rows_reject_entire_batch_without_partial_writes(self):
        first=self.add(category='Outros'); second=self.add(description='Mercado exemplo',category='Mercado')
        data=review_overview(self.store); by_id={row['id']:row for row in data['items']}
        items=[self.correction(by_id[first['id']],'Transporte'),self.correction(by_id[second['id']],'Compras')]
        self.store.update(second['id'],{**second,'amount':'20,00','excluded':False})
        with self.assertRaisesRegex(ValidationError,'nenhuma alteração'):
            apply_review(self.store,{'items':items})
        self.assertEqual(self.store.get(first['id'])['category'],'Outros')
        with self.assertRaises(ValidationError): apply_review(self.store,{'scope':'business','items':[items[0]]})
        self.add(category='Pagamento de fatura')
        protected=review_overview(self.store,include_excluded=True,category='Pagamento de fatura')['items'][0]
        with self.assertRaises(ValidationError): apply_review(self.store,{'items':[self.correction(protected,'Compras')]})

    def test_mixed_categories_at_one_merchant_are_not_learned_as_one_rule(self):
        self.add(category='Outros'); self.add(category='Outros')
        rows=review_overview(self.store)['items']
        result=apply_review(self.store,{'items':[self.correction(rows[0],'Compras'),self.correction(rows[1],'Mercado')]})
        self.assertEqual(result,{'applied':2,'learned':0})

    def test_expected_receipts_are_explicit_scoped_editable_finite_and_not_real_income(self):
        item={'scope':'personal','kind':'income','description':'Serviço esperado','amount':'1000,00',
              'due_day':31,'starts_month':'2026-10','ends_month':'2026-11'}
        saved=save_commitment(self.store,item)
        repeated=save_commitment(self.store,item)
        self.assertEqual(saved['id'],repeated['id']); self.assertTrue(repeated['already_registered'])
        october=calendar_overview(self.store,'personal',date(2026,10,1),days=92)
        self.assertEqual(october['expected_income_cents'],100000)
        self.assertEqual([row['due_on'] for row in october['events']],['2026-10-31','2026-11-30'])
        self.assertEqual(calendar_overview(self.store,'personal',date(2026,10,1),reference_month='2026-12')['expected_income_cents'],0)
        self.assertEqual(calendar_overview(self.store,'business',date(2026,10,1))['expected_income_cents'],0)
        updated=save_commitment(self.store,{**item,'amount':'1500,00'},commitment_id=saved['id'])
        self.assertEqual(updated['amount_cents'],150000)
        self.assertEqual(self.store.summary('2026-10')['income_cents'],0)
        with self.assertRaises(ValidationError): save_commitment(self.store,{**item,'ends_month':'2026-09'})

    def test_income_evidence_excludes_internal_transfers_cashback_and_refunds(self):
        for month in ['2026-07','2026-08','2026-09']:
            for description,category in [('Serviço recebido','Receita'),('Cashback exemplo','Cashback'),('Conta própria','Transferência entre contas')]:
                self.add(description=description,category=category,kind='income',occurred_on=month+'-10')
        data=history_insights(self.store,today=date(2026,10,14))
        self.assertEqual(len(data['recurring_income']),1)
        self.assertEqual(data['recurring_income'][0]['description'],'Serviço recebido')
        self.assertEqual(data['recurring_income'][0]['due_day_hint'],10)


if __name__=='__main__': unittest.main()
