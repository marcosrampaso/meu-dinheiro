from pathlib import Path
import tempfile
import unittest

from finance import FinanceStore, ValidationError


class PaymentViewTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory(); self.addCleanup(directory.cleanup)
        self.store = FinanceStore(Path(directory.name)/'fixture.db')
        for description,amount,category,kind,excluded,scope in [
            ('Débito','25','Mercado','expense',False,'personal'),
            ('Sem categoria','15','Importado','expense',False,'personal'),
            ('Fatura paga','80','Pagamento de fatura','expense',True,'personal'),
            ('Transferência','10','Transferência entre contas','expense',True,'personal'),
            ('Receita','1000','Receita','income',False,'personal'),
            ('Compra PJ','10','Mercado','expense',False,'business')]:
            self.store.add({'description':description,'amount':amount,'category':category,'kind':kind,
                'excluded':excluded,'scope':scope,'occurred_on':'2026-10-10'})
        self.store.add_card_purchase({'bank':'inter','account':'exemplo','invoice_month':'2026-10',
            'occurred_on':'2026-09-10','description':'Crédito','category':'Mercado','amount':'80'})

    def test_totals_and_categories_separate_consumption_without_double_counting_settlement(self):
        all_data = self.store.purchase_categories(month='2026-10')
        account = self.store.purchase_categories(month='2026-10',payment_type='account')
        credit = self.store.purchase_categories(month='2026-10',payment_type='credit')
        self.assertEqual(all_data['purchase_total_cents'],12000)
        self.assertEqual((account['purchase_total_cents'],credit['purchase_total_cents']),(4000,8000))
        self.assertEqual(account['breakdown']['account'],{'total_cents':4000,'uncategorized_cents':1500,'count':2})
        self.assertEqual(self.store.summary('2026-10')['spending_breakdown'],all_data['breakdown'])
        self.assertEqual(self.store.purchase_categories('business','2026-10')['purchase_total_cents'],1000)
        self.assertEqual(self.store.purchase_categories('all','2026-10')['purchase_total_cents'],13000)
        self.assertEqual(self.store.purchase_categories(month='2026-09',payment_type='credit')['purchase_total_cents'],0)

    def test_charts_filter_categories_and_keep_bank_cashflow_and_invoices_explicit(self):
        account = self.store.analytics('2026-10',months=1,payment_type='account')
        credit = self.store.analytics('2026-10',months=1,payment_type='credit')
        self.assertEqual(account['category_spending'][0]['total_cents'],4000)
        self.assertEqual(credit['category_spending'][0]['total_cents'],8000)
        self.assertEqual(account['cashflow'],credit['cashflow'])
        self.assertEqual(account['invoices'],credit['invoices'])
        self.assertEqual(credit['invoices'][0]['total_cents'],8000)
        self.assertEqual(len(self.store.list_month('2026-10',payment_type='credit')),1)
        self.assertTrue(all(row['invoice_month'] is None for row in self.store.list_month('2026-10',payment_type='account')))
        for operation in [lambda: self.store.list_month('2026-10',payment_type='invalid'),
                          lambda: self.store.analytics('2026-10',payment_type='invalid'),
                          lambda: self.store.purchase_categories(payment_type='invalid')]:
            with self.assertRaises(ValidationError): operation()

    def test_monthly_origins_sum_to_the_combined_bar_and_track_uncategorized(self):
        row=self.store.analytics('2026-10',months=1)['category_spending'][0]
        self.assertEqual(row['breakdown']['account'],{'total_cents':4000,'uncategorized_cents':1500})
        self.assertEqual(row['breakdown']['credit'],{'total_cents':8000,'uncategorized_cents':0})
        self.assertEqual(sum(v['total_cents'] for v in row['breakdown'].values()),row['total_cents'])
        self.assertEqual({r['category']:r['amount_cents'] for r in row['categories']},
            {'Mercado':10500,'Importado':1500})

    def test_all_history_respects_scope_origin_end_month_and_invoice_reference(self):
        self.store.add_card_purchase({'bank':'inter','account':'exemplo','invoice_month':'2025-01',
            'occurred_on':'2024-10-10','description':'Parcela antiga','category':'Mercado','amount':'5'})
        self.store.add({'kind':'expense','description':'PJ antigo','amount':'1','category':'Mercado',
            'occurred_on':'2024-01-10','scope':'business'})
        personal=self.store.analytics('2026-10',months='all',payment_type='credit')
        self.assertEqual((personal['months'][0],personal['months'][-1]),('2025-01','2026-10'))
        self.assertEqual(personal['category_spending'][0]['total_cents'],500)
        account=self.store.analytics('2026-10',months='all',payment_type='account')
        self.assertEqual(account['months'],['2026-10'])
        self.assertEqual(self.store.analytics('2026-10','all','all')['months'][0],'2024-01')
        self.assertEqual(self.store.analytics('2023-10',months='all')['months'],['2023-10'])
