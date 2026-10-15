import hashlib
import json
import tempfile
import unittest
from datetime import date
from unittest.mock import patch
from pathlib import Path

from finance import FinanceStore
from pluggy_inter import PluggyInter, _bank_classification, _bill_months, card_payment, live_start
from pluggy_sync import same_invoice
from pluggy_sync import restore_inter_history
from pluggy_xp import PluggyError, PluggyXP


XP_ID = "11111111-1111-1111-1111-111111111111"
INTER_ID = "22222222-2222-2222-2222-222222222222"
BANK_ID = "33333333-3333-3333-3333-333333333333"
CARD_ID = "44444444-4444-4444-4444-444444444444"
BILL_ID = "55555555-5555-5555-5555-555555555555"
OLD_BILL_ID = "66666666-6666-6666-6666-666666666666"


def transaction(identifier, date, description, amount, kind, **extra):
    return {"id": identifier, "date": date, "description": description, "amount": amount,
            "type": kind, "status": "POSTED", "currencyCode": "BRL", **extra}


class PluggyInterTests(unittest.TestCase):
    def setUp(self):
        clock = patch("pluggy_inter.date", wraps=date)
        self.clock = clock.start()
        self.clock.today.return_value = date(2026, 10, 13)
        self.addCleanup(clock.stop)
        self.directory = tempfile.TemporaryDirectory()
        base = Path(self.directory.name)
        self.store = FinanceStore(base / "financas.db")
        self.pluggy = PluggyXP(self.store, base / "pluggy-xp.json")
        self.pluggy.save_config({"client_id": "client", "client_secret": "secret",
                                 "item_id": XP_ID, "inter_item_id": INTER_ID})
        self.inter = PluggyInter(self.store, self.pluggy)

    def tearDown(self):
        self.directory.cleanup()

    def fake_http(self, method, path, payload=None, api_key=None):
        if path == "/auth":
            self.assertEqual(payload, {"clientId": "client", "clientSecret": "secret"})
            return {"apiKey": "session"}
        self.assertEqual(api_key, "session")
        if path.startswith("/accounts?"):
            return {"results": [
                {"id": BANK_ID, "type": "BANK", "name": "Conta Corrente",
                 "currencyCode": "BRL", "balance": 18.36, "updatedAt": "2026-10-13T12:00:00Z",
                 "number": "private-account-number", "taxNumber": "private-cpf"},
                {"id": CARD_ID, "type": "CREDIT", "name": "Cartão principal", "currencyCode": "BRL"},
            ], "totalPages": 1}
        if path.startswith("/bills?"):
            return {"results": [{"id": BILL_ID, "dueDate": "2026-10-20T00:00:00Z"},
                                {"id": OLD_BILL_ID, "dueDate": "2026-09-20T00:00:00Z"}],
                    "totalPages": 1}
        if path.startswith("/v2/transactions?") and BANK_ID in path:
            return {"results": [
                transaction("aaaaaaa1-aaaa-aaaa-aaaa-aaaaaaaaaaaa", "2026-09-20T00:00:00Z",
                            "Compra no débito", -20, "DEBIT"),
                transaction("aaaaaaa2-aaaa-aaaa-aaaa-aaaaaaaaaaaa", "2026-10-12T00:00:00Z",
                            "Mercado", -30, "DEBIT"),
                transaction("aaaaaaa3-aaaa-aaaa-aaaa-aaaaaaaaaaaa", "2026-10-12T00:00:00Z",
                            "Pagamento fatura cartão", -40, "DEBIT"),
            ], "next": None}
        if path.startswith("/v2/transactions?") and CARD_ID in path:
            return {"results": [
                transaction("bbbbbbb1-bbbb-bbbb-bbbb-bbbbbbbbbbbb", "2026-10-10T00:00:00Z",
                            "Restaurante", 25, "DEBIT", creditCardMetadata={"billId": BILL_ID}),
                transaction("bbbbbbb2-bbbb-bbbb-bbbb-bbbbbbbbbbbb", "2026-09-10T00:00:00Z",
                            "Fatura antiga", 50, "DEBIT", creditCardMetadata={"billId": OLD_BILL_ID}),
                transaction("bbbbbbb3-bbbb-bbbb-bbbb-bbbbbbbbbbbb", "2026-10-10T00:00:00Z",
                            "Pagamento recebido", -40, "CREDIT", operationType="PAGAMENTO_FATURA",
                            creditCardMetadata={"billId": BILL_ID}),
                transaction("bbbbbbb4-bbbb-bbbb-bbbb-bbbbbbbbbbbb", "2026-10-13T00:00:00Z",
                            "Compra pendente", 99, "DEBIT", status="PENDING",
                            creditCardMetadata={"billForecastDate": "2026-10"}),
            ], "next": None}
        raise AssertionError(path)

    def test_preview_preserves_historical_statements_and_keeps_data_private(self):
        self.store.save_account_statement("Inter · conta corrente", "personal",
                                          {"starts_on": "2026-09-09", "ends_on": "2026-10-09"}, [], None)
        self.store.add_imported([{"kind": "expense", "description": "Fatura antiga",
                                  "category": "Importado", "amount_cents": 5000,
                                  "occurred_on": "2026-09-10", "excluded": False,
                                  "source": "Inter · cartão principal", "invoice_month": "2026-09",
                                  "import_key": hashlib.sha256(b"old-statement").hexdigest(),
                                  "scope": "personal"}])
        self.store.save_invoice_coverage("Inter · cartão principal", "personal", "2026-09")
        preview = self.inter.preview(self.fake_http)
        self.assertEqual(preview["new_count"], 4)
        self.assertEqual(preview["replaced_count"], 0)
        self.assertEqual(preview["skipped"], {"historical": 2, "card_payment": 1})
        self.assertEqual(preview["bank_balance"]["amount_cents"], 1836)
        self.assertNotIn("private-cpf", json.dumps(preview))
        self.assertNotIn("private-account-number", json.dumps(preview))
        self.assertNotIn("secret", json.dumps(preview))
        with self.store._connect() as connection:
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM transactions").fetchone()[0], 1)
        with self.assertRaises(PluggyError):
            self.inter.import_preview("wrong-token")
        result = self.inter.import_preview(preview["preview_token"])
        self.assertEqual((result["imported"],result["replaced"],result["balance_updated"]), (4,0,True))
        self.assertTrue((self.store.path.parent / result["backup"]).exists())
        self.assertEqual(self.store.account_balance_snapshots("2026-10")[0]["amount_cents"], 1836)
        with self.store._connect() as connection:
            rows = [dict(row) for row in connection.execute(
                "SELECT description, category, excluded, invoice_month FROM transactions ORDER BY id")]
        self.assertEqual(len(rows), 5)
        self.assertIn({"description": "Fatura antiga", "category": "Importado", "excluded": 0, "invoice_month": "2026-09"}, rows)
        self.assertIn({"description": "Pagamento fatura cartão", "category": "Pagamento de fatura",
                       "excluded": 1, "invoice_month": None}, rows)
        repeat = self.inter.preview(self.fake_http)
        self.assertEqual(repeat["replaced_count"], 4)
        self.inter.import_preview(repeat["preview_token"])
        self.assertEqual(len(self.store.list_month("2026-10")), 4)

    def test_new_card_charge_in_same_month_can_be_synced_later(self):
        first = self.inter.preview(self.fake_http)
        self.inter.import_preview(first["preview_token"])

        def updated_http(method, path, payload=None, api_key=None):
            response = self.fake_http(method, path, payload, api_key)
            if path.startswith("/v2/transactions?") and CARD_ID in path:
                response["results"].append(transaction(
                    "bbbbbbb5-bbbb-bbbb-bbbb-bbbbbbbbbbbb", "2026-10-13T00:00:00Z",
                    "Nova compra", 12, "DEBIT", creditCardMetadata={"billId": BILL_ID}))
            return response

        second = self.inter.preview(updated_http)
        self.assertEqual(second["new_count"], 5)
        self.assertEqual(second["new_items"][-1]["description"], "Nova compra")

    def test_closed_previous_cycle_and_open_forecast_are_not_combined(self):
        # Purchases in September: closed in September, payable in October.
        # October's open cycle must not also include that closed September bill.
        for index, amount in enumerate((10,20,30)):
            self.store.add_card_purchase({'bank':'inter','account':'cartão principal',
                'invoice_month':'2026-09','occurred_on':f'2026-09-{10+index}',
                'description':f'Loja exemplo {index}','amount':str(amount),'category':'Mercado'})
        self.store.save_invoice_coverage('Inter · cartão principal','personal','2026-09')
        def cycles(method,path,payload=None,api_key=None):
            result=self.fake_http(method,path,payload,api_key)
            if path.startswith('/bills?'):
                result['results']=[{'id':BILL_ID,'dueDate':'2026-10-10',
                    'billClosingDate':'2026-09-30','totalAmount':60}]
            if path.startswith('/v2/transactions?') and CARD_ID in path:
                result['results']=[transaction(f'bbbbbbb{index+1}-bbbb-bbbb-bbbb-bbbbbbbbbbbb',
                    f'2026-09-{10+index}',f'Loja exemplo {index}',amount,'DEBIT',
                    creditCardMetadata={'billId':BILL_ID}) for index,amount in enumerate((10,20,30))]
                result['results'].append(transaction('bbbbbbb9-bbbb-bbbb-bbbb-bbbbbbbbbbbb',
                    '2026-10-10','Compra atual',40,'DEBIT',status='PENDING',
                    creditCardMetadata={'billForecastDate':'2026-10'}))
            return result
        for _ in range(2):
            preview=self.inter.preview(cycles,full_card_history=True)
            self.assertEqual(preview['skipped']['existing_invoice'],3)
            self.inter.import_preview(preview['preview_token'])
            self.assertEqual(len(self.store.invoice_items('2026-09','Inter · cartão principal')),3)
            current=self.store.invoice_items('2026-10','Inter · cartão principal')
            self.assertEqual((len(current),sum(r['amount_cents'] for r in current)),(1,4000))

    def test_bill_cycle_uses_closing_date_and_recognizes_pdf_when_closing_is_absent(self):
        bill={'id':BILL_ID,'dueDate':'2026-10-10','billClosingDate':'2026-09-30'}
        self.assertEqual(_bill_months([bill],[],{}),{BILL_ID:'2026-09'})
        bill.pop('billClosingDate')
        existing=[{'kind':'expense','description':f'Loja exemplo {i}','amount_cents':1000+i,
            'occurred_on':'2026-09-10','import_key':f'pdf-{i}','category':'Mercado'} for i in range(10)]
        remote=[transaction(f'bbbbbbb{i}-bbbb-bbbb-bbbb-bbbbbbbbbbbb','2026-09-10',
            row['description'],row['amount_cents']/100,'DEBIT',creditCardMetadata={'billId':BILL_ID})
            for i,row in enumerate(existing)]
        self.assertEqual(_bill_months([bill],remote,{'2026-09':existing}),{BILL_ID:'2026-09'})
        self.assertTrue(same_invoice(existing,existing))
        self.assertFalse(same_invoice(existing,existing[:-1]))
        changed=[{**row,'amount_cents':row['amount_cents']+1} for row in existing]
        self.assertFalse(same_invoice(changed,existing))
        # Repeated matching to one merchant/date must not masquerade as a whole bill.
        repeated=[{**row,'description':'Loja exemplo','amount_cents':1000} for row in existing]
        other=[{**row,'description':'Outra compra','amount_cents':1000} for row in existing]
        other[0]=repeated[0]
        self.assertFalse(same_invoice(repeated,other))

    def test_unknown_closed_cycle_cannot_be_added_to_an_open_forecast(self):
        rows=[transaction('bbbbbbb1-bbbb-bbbb-bbbb-bbbbbbbbbbbb','2026-09-10','Loja',10,'DEBIT',
            creditCardMetadata={'billId':BILL_ID}),
            transaction('bbbbbbb2-bbbb-bbbb-bbbb-bbbbbbbbbbbb','2026-10-10','Compra aberta',20,'DEBIT',
                status='PENDING',creditCardMetadata={'billForecastDate':'2026-10'})]
        with self.assertRaisesRegex(PluggyError,'pode se misturar'):
            _bill_months([{'id':BILL_ID,'dueDate':'2026-10-10'}],rows,{})

    def test_duplicates_are_replaced_but_categories_and_pj_survive(self):
        self.store.add_card_purchase({"bank":"inter","account":"cartão principal","invoice_month":"2026-10",
            "description":"Restaurante","category":"Alimentação","amount":"25,00","occurred_on":"2026-10-10"})
        self.store.add_imported([{"kind":"expense","description":"Restaurante","category":"Importado",
            "amount_cents":2500,"occurred_on":"2026-10-10","source":"Inter · cartão principal",
            "invoice_month":"2026-11","scope":"personal","import_key":hashlib.sha256(b"legacy").hexdigest()}])
        pj = self.store.add({"kind":"expense","description":"Empresa","category":"Outros","amount":"25,00",
                             "occurred_on":"2026-10-10","scope":"business"})
        preview = self.inter.preview(self.fake_http)
        self.inter.import_preview(preview["preview_token"])
        rows = [r for r in self.store.list_month("2026-10") if r["description"] == "Restaurante"]
        self.assertEqual(len(rows),1)
        self.assertEqual(rows[0]["category"],"Alimentação")
        self.assertEqual(self.store.get(pj["id"])["scope"],"business")

    def test_refresh_removes_deleted_rows_and_updates_amounts(self):
        first = self.inter.preview(self.fake_http)
        self.inter.import_preview(first["preview_token"])
        def changed(method,path,payload=None,api_key=None):
            result = self.fake_http(method,path,payload,api_key)
            if path.startswith("/v2/transactions?") and BANK_ID in path:
                result["results"] = result["results"][1:]
                result["results"][0]["amount"] = -31
            return result
        second = self.inter.preview(changed)
        self.inter.import_preview(second["preview_token"])
        self.assertEqual(self.store.list_month("2026-09"), [])
        mercado = next(r for r in self.store.list_month("2026-10") if r["description"]=="Mercado")
        self.assertEqual(mercado["amount_cents"],3100)

    def test_full_account_history_replaces_overlap_preserves_categories_and_invoice_policy(self):
        manual = self.store.add({'kind':'expense','description':'Compra no débito','category':'Tabaco',
            'amount':'20','occurred_on':'2026-09-20','scope':'personal'})
        with self.store._connect() as c:
            c.execute("UPDATE transactions SET source='Inter · conta corrente' WHERE id=?",(manual['id'],))
        old_card = {'kind':'expense','description':'Fatura antiga','category':'Mercado','amount_cents':5000,
            'occurred_on':'2026-09-10','source':'Inter · cartão principal','invoice_month':'2026-09',
            'scope':'personal','excluded':False,'import_key':hashlib.sha256(b'closed-invoice').hexdigest()}
        self.store.add_imported([old_card])
        self.store.save_invoice_coverage(old_card['source'],'personal','2026-09')
        preview = self.inter.preview(self.fake_http, full_history=True)
        self.assertTrue(preview['full_history'])
        self.assertEqual(preview['account_history_start'],'2026-09-20')
        self.assertTrue(any(p['starts_on']=='2026-09-20' for p in preview['periods']))
        result = self.inter.import_preview(preview['preview_token'])
        self.assertEqual(result['categories_preserved'],1)
        with self.store._connect() as c:
            rows = [dict(r) for r in c.execute("SELECT * FROM transactions WHERE source='Inter · conta corrente' AND occurred_on='2026-09-20'")]
            self.assertEqual(len(rows),1)
            self.assertEqual(rows[0]['category'],'Tabaco')
            self.assertEqual(c.execute('SELECT COUNT(*) FROM category_examples').fetchone()[0],1)
            self.assertEqual(c.execute('SELECT starts_on,card_starts_on,account_history_start FROM pluggy_inter_policy').fetchone()[:],
                ('2026-10-01','2026-10-01','2026-09-20'))
        repeat = self.inter.preview(self.fake_http)
        self.assertEqual(repeat['skipped']['historical'],1) # Only the old card purchase.
        self.inter.import_preview(repeat['preview_token'])
        with self.store._connect() as c:
            self.assertEqual(c.execute('SELECT COUNT(*) FROM category_examples').fetchone()[0],1)
            self.assertEqual(c.execute("SELECT COUNT(*) FROM transactions WHERE invoice_month='2026-09'").fetchone()[0],1)
            self.assertEqual(c.execute("SELECT category FROM transactions WHERE invoice_month='2026-09'").fetchone()[0],'Mercado')
        self.assertEqual(next(r for r in self.store.list_month('2026-09') if r['invoice_month'] is None)['category'],'Tabaco')

    def test_complete_card_history_uses_invoice_month_keeps_pdfs_and_manual_categories(self):
        august_bill = '77777777-7777-7777-7777-777777777777'
        pdf = self.store.add_card_purchase({'bank':'inter','account':'cartão principal','invoice_month':'2026-09',
            'occurred_on':'2026-09-10','description':'Fatura antiga','amount':'70','category':'Mercado'})
        self.store.save_invoice_coverage('Inter · cartão principal','personal','2026-09')
        manual = self.store.add_card_purchase({'bank':'inter','account':'cartão principal','invoice_month':'2026-08',
            'occurred_on':'2026-05-10','description':'Compra parcelada','amount':'30','category':'Lazer'})
        def history_http(method,path,payload=None,api_key=None):
            result=self.fake_http(method,path,payload,api_key)
            if path.startswith('/bills?'):
                result['results'].append({'id':august_bill,'dueDate':'2026-08-20'})
            if path.startswith('/v2/transactions?') and CARD_ID in path:
                result['results'].append(transaction('ddddddd1-dddd-dddd-dddd-dddddddddddd',
                    '2026-05-10','Compra parcelada',30,'DEBIT',creditCardMetadata={'billId':august_bill}))
            return result
        preview=self.inter.preview(history_http,full_card_history=True)
        period=next(p for p in preview['periods'] if p['source']=='Inter · cartão principal')
        self.assertEqual(period['basis'],'invoice')
        self.assertEqual(period['invoice_months'],['2026-08','2026-10'])
        self.assertEqual(preview['skipped']['existing_invoice'],1)
        self.assertTrue(any(b['preserved'] for b in preview['card_invoices'] if b['month']=='2026-09'))
        self.inter.import_preview(preview['preview_token'])
        self.assertEqual(self.store.get(pdf['id'])['amount_cents'],7000)
        august=self.store.invoice_items('2026-08','Inter · cartão principal')
        self.assertEqual(len(august),1)
        self.assertEqual((august[0]['category'],august[0]['occurred_on']),('Lazer','2026-05-10'))
        # Future webhook/normal syncs retain the expanded card history policy.
        repeat=self.inter.preview(history_http)
        self.assertTrue(repeat['full_card_history'])
        self.inter.import_preview(repeat['preview_token'])
        self.assertEqual(len(self.store.invoice_items('2026-08','Inter · cartão principal')),1)
        self.assertEqual(self.store.get(pdf['id'])['amount_cents'],7000)
        with self.store._connect() as c:
            self.assertEqual(c.execute("SELECT provider FROM invoice_coverage WHERE invoice_month='2026-08'").fetchone()[0],'pluggy')

    def test_missing_card_and_missing_bill_details_are_explicit_without_erasing_history(self):
        def no_card(method,path,payload=None,api_key=None):
            result=self.fake_http(method,path,payload,api_key)
            if path.startswith('/accounts?'): result['results']=result['results'][:1]
            return result
        missing=self.inter.preview(no_card,full_card_history=True)
        self.assertTrue(any('não retornou cartão' in text for text in missing['warnings']))
        initial=self.inter.preview(self.fake_http,full_card_history=True)
        self.inter.import_preview(initial['preview_token'])
        def missing_details(method,path,payload=None,api_key=None):
            result=self.fake_http(method,path,payload,api_key)
            if path.startswith('/v2/transactions?') and CARD_ID in path:
                result['results']=[r for r in result['results'] if r['creditCardMetadata'].get('billId') != OLD_BILL_ID]
            return result
        incomplete=self.inter.preview(missing_details)
        self.assertTrue(any('2026-09' in text and 'sem compras' in text for text in incomplete['warnings']))
        self.inter.import_preview(incomplete['preview_token'])
        self.assertEqual(len(self.store.invoice_items('2026-09','Inter · cartão principal')),1)

    def test_explicitly_zero_provider_bill_can_remove_old_items_only_in_that_invoice(self):
        initial=self.inter.preview(self.fake_http,full_card_history=True)
        self.inter.import_preview(initial['preview_token'])
        def zero_bill(method,path,payload=None,api_key=None):
            result=self.fake_http(method,path,payload,api_key)
            if path.startswith('/bills?'):
                next(b for b in result['results'] if b['id']==OLD_BILL_ID)['totalAmount']=0
            if path.startswith('/v2/transactions?') and CARD_ID in path:
                result['results']=[r for r in result['results'] if r['creditCardMetadata'].get('billId') != OLD_BILL_ID]
            return result
        preview=self.inter.preview(zero_bill)
        period=next(p for p in preview['periods'] if p['source']=='Inter · cartão principal')
        self.assertIn('2026-09',period['invoice_months'])
        self.inter.import_preview(preview['preview_token'])
        self.assertEqual(self.store.invoice_items('2026-09','Inter · cartão principal'),[])
        self.assertEqual(len(self.store.invoice_items('2026-10','Inter · cartão principal')),2)

    def test_card_history_rejects_invalid_flag_before_network_and_unlinked_rows_before_import(self):
        with self.assertRaises(PluggyError): self.inter.preview(self.fake_http,full_card_history='yes')
        def unlinked(method,path,payload=None,api_key=None):
            result=self.fake_http(method,path,payload,api_key)
            if path.startswith('/v2/transactions?') and CARD_ID in path:
                result['results'][1]['creditCardMetadata']={}
            return result
        with self.assertRaisesRegex(PluggyError,'sem mês de fatura'):
            self.inter.preview(unlinked,full_card_history=True)
        self.assertEqual(self.store.list_month('2026-09'),[])

    def test_full_history_flag_validates_before_network_and_preview_does_not_change_policy(self):
        with self.assertRaises(PluggyError): self.inter.preview(self.fake_http, full_history='true')
        preview = self.inter.preview(self.fake_http, full_history=True)
        with self.store._connect() as c:
            self.assertEqual(c.execute('SELECT COUNT(*) FROM pluggy_inter_policy').fetchone()[0],0)
        self.assertTrue(preview['full_history'])

    def test_config_can_add_inter_without_reentering_secret(self):
        self.pluggy.delete_config()
        self.pluggy.save_config({"client_id": "client", "client_secret": "secret", "item_id": XP_ID})
        self.pluggy.save_config({"inter_item_id": INTER_ID})
        self.assertTrue(self.pluggy.configured())
        self.assertTrue(self.pluggy.inter_configured())
        self.assertEqual(self.pluggy.load_config()["client_secret"], "secret")

    def test_unknown_card_month_aborts_before_replacing(self):
        def broken(method,path,payload=None,api_key=None):
            result=self.fake_http(method,path,payload,api_key)
            if path.startswith('/v2/transactions?') and CARD_ID in path:
                result['results'][0]['creditCardMetadata']={}
            return result
        with self.assertRaises(PluggyError):
            self.inter.preview(broken)
        with self.store._connect() as connection:
            self.assertEqual(connection.execute('SELECT COUNT(*) FROM transactions').fetchone()[0],0)

    def test_old_preview_cannot_overwrite_newer_inter_data(self):
        old = self.inter.preview(self.fake_http)
        automatic = PluggyInter(self.store,self.pluggy)
        current = automatic.preview(self.fake_http)
        automatic.import_preview(current['preview_token'])
        with self.assertRaisesRegex(PluggyError,'após esta prévia'):
            self.inter.import_preview(old['preview_token'])
        self.assertEqual(len(self.store.list_month('2026-10')),4)

    def test_investment_application_and_cashback_are_not_consumption(self):
        self.assertEqual(_bank_classification({"operationType":"OUTROS"},
            "Aplicação - Cdb Exemplo Banco", "expense"),("Movimento de investimento",True))
        self.assertEqual(_bank_classification({},"Cashback - Exemplo","income"),("Cashback",True))
        self.assertEqual(_bank_classification({},"Compra no débito - Cdb Restaurante","expense"),("Importado",False))

    def test_online_settlement_is_not_a_refund_but_merchant_refunds_survive(self):
        self.assertTrue(card_payment({}, "PAGAMENTO ON LINE"))
        self.assertFalse(card_payment({}, "PAGAMENTO TRANSPORTE"))
        def extra_http(method, path, payload=None, api_key=None):
            result = self.fake_http(method, path, payload, api_key)
            if path.startswith('/v2/transactions?') and CARD_ID in path:
                result['results'].extend([
                    transaction('ccccccc1-cccc-cccc-cccc-cccccccccccc', '2026-10-11',
                                'PAGAMENTO ON LINE', -900, 'CREDIT',
                                creditCardMetadata={'billForecastDate': '2026-10'}),
                    transaction('ccccccc2-cccc-cccc-cccc-cccccccccccc', '2026-10-11',
                                'Loja devolução', -5, 'CREDIT',
                                creditCardMetadata={'billForecastDate': '2026-10'}),
                ])
            return result
        preview = self.inter.preview(extra_http)
        self.assertEqual(preview['skipped']['card_payment'], 2)
        self.inter.import_preview(preview['preview_token'])
        invoice = self.store.invoice_history()[0]
        self.assertEqual(invoice['expense_cents'] - invoice['credit_cents'], 11900)

    def test_live_month_remains_fixed_when_month_changes(self):
        preview = self.inter.preview(self.fake_http)
        self.inter.import_preview(preview['preview_token'])
        self.clock.today.return_value = date(2026, 11, 13)
        self.assertEqual(live_start(self.store), '2026-10-01')
        again = self.inter.preview(self.fake_http)
        self.assertEqual(again['live_starts_on'], '2026-10-01')
        self.assertTrue(all(p['starts_on'] >= '2026-10-01' for p in again['periods']))

    def test_recovery_keeps_original_invoice_live_purchases_and_other_sources(self):
        original = {'kind': 'expense', 'description': 'Restaurante histórico',
                    'category': 'Alimentação', 'amount_cents': 5000,
                    'occurred_on': '2026-09-10', 'excluded': False,
                    'source': 'Inter · cartão principal', 'invoice_month': '2026-09',
                    'import_key': hashlib.sha256(b'original-pdf').hexdigest(), 'scope': 'personal'}
        shifted = {**original, 'import_key': hashlib.sha256(b'provider-duplicate').hexdigest(),
                   'invoice_month': '2026-10', 'category': 'Importado'}
        self.store.add_imported([shifted])
        pj = self.store.add({'kind': 'expense', 'description': 'Empresa', 'category': 'Outros',
                             'amount': '15,00', 'occurred_on': '2026-09-10', 'scope': 'business'})
        preview = self.inter.preview(self.fake_http)
        self.inter.import_preview(preview['preview_token'])
        result = restore_inter_history(self.store, [original], '2026-10-01')
        self.assertTrue((self.store.path.parent / result['backup']).exists())
        self.assertEqual(self.store.invoice_history()[-1]['expense_cents'] - self.store.invoice_history()[-1]['credit_cents'], 5000)

        self.assertEqual(self.store.get(pj['id'])['scope'], 'business')
        before = self.store.purchase_categories('personal')
        again = self.inter.preview(self.fake_http)
        self.inter.import_preview(again['preview_token'])
        self.assertEqual(self.store.purchase_categories('personal'), before)
        self.assertEqual(self.store.invoice_history()[-1]['expense_cents'] - self.store.invoice_history()[-1]['credit_cents'], 5000)

    def test_open_bill_includes_month_end_purchase_once_after_posting(self):
        original = {'kind': 'expense', 'description': 'Compra antiga', 'category': 'Mercado',
                    'amount_cents': 5000, 'occurred_on': '2026-09-29', 'excluded': False,
                    'source': 'Inter · cartão principal', 'invoice_month': '2026-09',
                    'import_key': hashlib.sha256(b'closed-pdf').hexdigest(), 'scope': 'personal'}
        restore_inter_history(self.store, [original], '2026-10-01')
        def month_end_http(method, path, payload=None, api_key=None):
            result = self.fake_http(method, path, payload, api_key)
            if path.startswith('/v2/transactions?') and CARD_ID in path:
                result['results'].append(transaction(
                    'ccccccc3-cccc-cccc-cccc-cccccccccccc', '2026-09-30', 'Compra nova', 10,
                    'DEBIT', status='PENDING', creditCardMetadata={'billForecastDate': '2026-10'}))
            return result
        preview = self.inter.preview(month_end_http)
        self.assertEqual(preview['card_live_starts_on'], '2026-09-30')
        self.inter.import_preview(preview['preview_token'])
        def posted_http(method, path, payload=None, api_key=None):
            result = month_end_http(method, path, payload, api_key)
            if path.startswith('/v2/transactions?') and CARD_ID in path:
                result['results'][-1]['status'] = 'POSTED'
            return result
        again = self.inter.preview(posted_http)
        self.inter.import_preview(again['preview_token'])
        with self.store._connect() as connection:
            rows = connection.execute("SELECT * FROM transactions WHERE description='Compra nova'").fetchall()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]['pending'], 0)
        self.assertEqual(self.store.invoice_history()[-1]['expense_cents'], 5000)


if __name__ == "__main__":
    unittest.main()
