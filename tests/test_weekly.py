import json
import tempfile
import unittest
from datetime import date
from pathlib import Path
from unittest.mock import patch

from finance import FinanceStore
from weekly import (WeeklyError, generate_weekly_report, saved_weekly_report, save_weekly_report,
                    weekly_context, weekly_transactions, month_to_date_comparison)


class WeeklyTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.store = FinanceStore(Path(self.directory.name) / "test.db")

    def tearDown(self):
        self.directory.cleanup()

    def test_aggregates_completed_week_by_scope_without_internal_transfers(self):
        for occurred_on, amount, category, scope, excluded in [
            ("2026-10-05", "30,00", "Mercado", "personal", False),
            ("2026-10-06", "10,00", "Importado", "personal", False),
            ("2026-09-28", "20,00", "Mercado", "personal", False),
            ("2026-10-05", "100,00", "Outros", "business", False),
            ("2026-10-05", "500,00", "Outros", "personal", True),
            ("2026-10-12", "60,00", "Lazer", "personal", False),
        ]:
            self.store.add({"kind": "expense", "description": "Exemplo", "amount": amount,
                            "category": category, "occurred_on": occurred_on,
                            "scope": scope, "excluded": excluded})
        context = weekly_context(self.store, "personal", date(2026, 10, 13))
        self.assertEqual((context["starts_on"], context["ends_on"]), ("2026-10-05", "2026-10-11"))
        self.assertEqual((context["current_total_cents"], context["previous_total_cents"]), (4000, 2000))
        self.assertEqual(context["categories"][0]["category"], "Mercado")
        self.assertEqual(weekly_context(self.store, "all", date(2026, 10, 13))["current_total_cents"], 14000)
        report = {"headline": "Mercado liderou", "insights": ["Mercado foi o maior gasto.", "Revise os não categorizados."]}
        save_weekly_report(self.store, context, report)
        self.assertEqual(saved_weekly_report(self.store, context)["headline"], "Mercado liderou")
        self.store.add({"kind": "expense", "description": "Outro", "amount": "1,00",
                        "category": "Lazer", "occurred_on": "2026-10-07"})
        self.assertIsNone(saved_weekly_report(self.store, weekly_context(self.store, "personal", date(2026, 10, 13))))

    def test_weekly_details_reconcile_with_totals_and_keep_ai_payload_aggregated(self):
        for description, category, amount, kind, excluded, scope, day in [
            ('Compra local privada', 'Mercado', '30', 'expense', False, 'personal', '2026-10-12'),
            ('Sem categoria', 'Importado', '5', 'expense', False, 'personal', '2026-10-18'),
            ('Pagamento', 'Pagamento de fatura', '90', 'expense', False, 'personal', '2026-10-12'),
            ('Investimento', 'Transferência para investimento', '100', 'expense', False, 'personal', '2026-10-12'),
            ('Ignorado', 'Outros', '500', 'expense', True, 'personal', '2026-10-12'),
            ('Receita', 'Receita', '1000', 'income', False, 'personal', '2026-10-12'),
            ('Outra semana', 'Lazer', '10', 'expense', False, 'personal', '2026-10-11'),
            ('Empresa', 'Serviços', '50', 'expense', False, 'business', '2026-10-12'),
        ]:
            self.store.add({'description':description, 'category':category, 'amount':amount, 'kind':kind,
                            'excluded':excluded, 'scope':scope, 'occurred_on':day})
        self.store.add_card_purchase({'bank':'inter', 'account':'exemplo', 'description':'Cartão privado',
            'category':'Assinaturas', 'amount':'20', 'occurred_on':'2026-10-13', 'invoice_month':'2026-11'})
        context = weekly_context(self.store, 'personal', date(2026,10,20))
        details = weekly_transactions(self.store, context)
        self.assertEqual(sum(row['amount_cents'] for row in details), context['current_total_cents'])
        self.assertEqual(len(details),3)
        self.assertEqual(details[0]['category'],'Não categorizado')
        self.assertEqual([row['payment_type'] for row in details],['account','credit','account'])
        self.assertNotIn('description', json.dumps(context))
        self.assertNotIn('import_key', json.dumps(details))
        all_context = weekly_context(self.store, 'all', date(2026,10,20))
        self.assertEqual(sum(r['amount_cents'] for r in weekly_transactions(self.store,all_context)),10500)
        payload = {'status':'completed','output':[]}
        output = json.dumps({'headline':'Mercado liderou','insights':['Observe o mercado.','Revise os não categorizados.']})
        with patch('weekly.urlopen') as remote, patch('weekly._read_completed_response',return_value=(payload,output)):
            generate_weekly_report({**context, 'transactions':details}, 'test-token','gpt-6-luna')
            request = json.loads(remote.call_args.args[0].data)
        sent = json.loads(request['input'][1]['content'])
        self.assertNotIn('transactions', sent)
        self.assertNotIn('privad', json.dumps(sent))

    def test_monthly_comparison_uses_same_purchase_day_and_excludes_settlements(self):
        for day, amount, scope, category in [
            ('2026-10-20','10','personal','Mercado'), ('2026-09-20','30','personal','Mercado'),
            ('2026-08-01','60','personal','Mercado'), ('2026-09-21','900','personal','Lazer'),
            ('2026-10-20','50','business','Serviços'),
            ('2026-10-19','100','personal','Pagamento de fatura'),
        ]:
            self.store.add({'kind':'expense','description':'Exemplo','category':category,
                'amount':amount,'scope':scope,'occurred_on':day})
        self.store.add_card_purchase({'bank':'inter','account':'exemplo','description':'Outra fatura',
            'category':'Lazer','amount':'10','occurred_on':'2026-10-19','invoice_month':'2026-11'})
        result = month_to_date_comparison(self.store,'personal',date(2026,10,20))
        self.assertEqual([p['ends_on'] for p in result['periods']],['2026-10-20','2026-09-20','2026-08-20'])
        self.assertEqual([p['total_cents'] for p in result['periods']],[2000,3000,6000])
        self.assertEqual(result['periods'][0]['credit_cents'],1000)
        self.assertEqual(result['periods'][1]['change_cents'],-1000)
        self.assertEqual(result['periods'][1]['change_percent'],-33.3)
        self.assertEqual(month_to_date_comparison(self.store,'all',date(2026,10,20))['periods'][0]['total_cents'],7000)

    def test_comparison_clamps_short_months_and_handles_year_boundaries_without_division_by_zero(self):
        leap = month_to_date_comparison(self.store,'personal',date(2024,3,31))
        self.assertEqual([p['ends_on'] for p in leap['periods']],['2024-03-31','2024-02-29','2024-01-31'])
        january = month_to_date_comparison(self.store,'personal',date(2026,1,1))
        self.assertEqual([p['ends_on'] for p in january['periods']],['2026-01-01','2025-12-01','2025-11-01'])
        self.assertIsNone(january['periods'][1]['change_percent'])

    def test_rejects_model_numbers_not_supported_by_display(self):
        context = {"starts_on": "2026-10-05", "ends_on": "2026-10-11",
                   "current_total_cents": 1000, "previous_total_cents": 500, "categories": []}
        payload = {"status": "completed", "output": [{"type": "message", "content": [
            {"type": "output_text", "text": json.dumps({"headline": "Gastou 90% mais",
                "insights": ["Mercado subiu.", "Observe lazer."]})}]}]}
        with patch("weekly.urlopen") as open_request:
            open_request.return_value.__enter__.return_value = []
            with patch("weekly._read_completed_response", return_value=(payload, "")):
                with self.assertRaises(WeeklyError):
                    generate_weekly_report(context, "token", "gpt-6-luna")


if __name__ == "__main__":
    unittest.main()
