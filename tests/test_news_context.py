from datetime import date
from pathlib import Path
import json
import tempfile
import unittest

from finance import FinanceStore
from news_context import financial_context


class NewsContextTests(unittest.TestCase):
    def setUp(self):
        directory=tempfile.TemporaryDirectory(); self.addCleanup(directory.cleanup)
        self.store=FinanceStore(Path(directory.name)/'fixture.db')
        self.store.import_xp_positions({'as_of':'2026-10-13','total_cents':10000,'assets_cents':10000,
            'receivables_cents':0,'cash_cents':0,'assets':[
                {'name':'ABCD11','category':'Ações','value_cents':10000,'cost_cents':9000,'quantity':'1','net_cents':None}]})
        self.store.save_account_balance('Conta pessoal sigilosa','personal','2026-10-13',12000)
        self.store.save_account_balance('Empresa sigilosa','business','2026-10-13',9999999)
        for scope,category,amount,description in [('personal','Lazer','50','Estabelecimento secreto'),
                ('business','Lazer','999','Despesa da empresa')]:
            self.store.add({'scope':scope,'kind':'expense','amount':amount,'category':category,
                'description':description,'occurred_on':'2026-09-10'})

    def test_only_personal_totals_no_cash_as_investable_no_identifiers(self):
        result=financial_context(self.store,date(2026,10,15))
        serialized=json.dumps(result)
        for forbidden in ('Conta pessoal sigilosa','Empresa sigilosa','Estabelecimento secreto','Despesa da empresa','9999999'):
            self.assertNotIn(forbidden,serialized)
        self.assertEqual(result['cash']['balance_cents'],12000)
        self.assertIsNone(result['cash']['investable_cents'])
        self.assertIsNone(result['reserve'])
        self.assertEqual(result['portfolio']['assets'][0]['gain_cents'],1000)
        self.assertEqual(result['spending']['saving_scenario']['monthly_cents'],500)
        self.assertEqual(result['spending']['saving_scenario']['annual_cents'],6000)
        self.assertEqual(result['risk_profile'],'not_recorded')

    def test_consent_modes_and_context_changes_keep_last_report_but_mark_stale(self):
        private=self.store.investment_news_context(True)
        public=self.store.investment_news_context(False)
        self.assertNotEqual(private['signature'],public['signature'])
        self.assertIsNone(public['financial_context'])
        report={'assets':[],'opportunities':[],'uses_finances':True,'financial_context':private['financial_context']}
        self.store.save_investment_news(private,report,'2026-10-15T12:00:00Z','gpt-6.1-sol')
        self.assertFalse(self.store.investment_news(True)['report_stale'])
        self.assertIsNone(self.store.investment_news(False)['report'])
        self.store.save_account_balance('Conta pessoal sigilosa','personal','2026-10-14',11000)
        changed=self.store.investment_news(True)
        self.assertTrue(changed['report_stale'])
        self.assertEqual(changed['financial_context']['cash']['balance_cents'],11000)
        self.assertEqual(changed['report']['financial_context']['cash']['balance_cents'],12000)
