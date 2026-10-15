"""Isolated UI fixture. Fake authentication and synthetic financial records only."""

from datetime import date
import calendar
from http.server import ThreadingHTTPServer
from pathlib import Path
import signal
import sys
import tempfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app import FinanceHandler
from finance import FinanceStore
from chatgpt_auth import ChatGPTAuth
from cdi import CDIBenchmark
from market_quotes import MarketQuotes
from pluggy_xp import PluggyXP
from pluggy_inter import PluggyInter
from webhook_client import WebhookClient
from local_auth import LocalAuth
from plan_insights import _months


def seed_history(store):
    months = _months(date.today(), 3)
    for month in months:
        end = calendar.monthrange(int(month[:4]), int(month[5:]))[1]
        store.save_account_statement("Conta exemplo", "personal", {"starts_on":month+'-01',
                                    "ends_on":f'{month}-{end}'}, [])
        store.save_invoice_coverage("Inter · cartão exemplo", "personal", month)
        for description, category, amount, kind in [
            ("Moradia exemplo","Moradia","1000,00","expense"),
            ("Mercado exemplo","Mercado","400,00","expense"),
            ("Serviço mensal exemplo","Assinaturas","50,00","expense"),
            ("Loja a revisar","Outros","30,00","expense"),
            ("Receita exemplo","Receita","3500,00","income"),
            ("Pagamento da fatura exemplo","Pagamento de fatura","200,00","expense")]:
            row=store.add({"kind":kind,"description":description,"category":category,"amount":amount,
                           "scope":"personal","occurred_on":month+'-10'})
            with store._connect() as c:
                c.execute("UPDATE transactions SET source='Conta exemplo' WHERE id=?",(row['id'],))
        store.add_card_purchase({"bank":"inter","account":"cartão exemplo","scope":"personal",
            "invoice_month":month,"occurred_on":months[0]+'-10',"description":"Lazer parcelado exemplo",
            "amount":"200,00","category":"Lazer"})


class FixtureAuth:
    def valid(self, token):
        return token == "synthetic-ui-session"

    def unlock(self):
        return "synthetic-ui-session"

    def lock(self, token):
        pass

    def session_info(self, token):
        return {"principal_id":"synthetic-owner","method":"touch_id","session_seconds":300,"remaining_seconds":300} if self.valid(token) else None

    def record_activity(self, token, idle_for_ms=0):
        return {"session_seconds":300,"remaining_seconds":300} if self.valid(token) else None


class FixtureServer(ThreadingHTTPServer):
    request_queue_size = 64


class PasswordFixtureAuth(LocalAuth):
    """Exercise real password/session logic, with no native biometric calls."""
    def _prepare(self):
        pass

    def unlock(self):
        return self._create_session("touch_id")


class IdleFixtureAuth(PasswordFixtureAuth):
    def _now(self):
        return self.fixture_now

    def __init__(self, *args):
        self.fixture_now = 1000.0
        super().__init__(*args)


class IdleFixtureHandler(FinanceHandler):
    # Test-only clock controls; never used by the application server.
    def do_POST(self):
        if self.path == "/test/advance":
            data = self._read_json()
            seconds = data.get("seconds", 0)
            assert isinstance(seconds, int) and 0 <= seconds <= 3600
            self.server.local_auth.fixture_now += seconds
            self._json(200, {"advanced":seconds})
        else:
            super().do_POST()


def stop(signum, frame):
    raise SystemExit(0)


def main():
    signal.signal(signal.SIGTERM, stop)
    with tempfile.TemporaryDirectory(prefix="finance-ui-fixture-") as directory:
        base = Path(directory)
        store = FinanceStore(base / "fixture.db")
        store.save_account_balance("Conta demonstração", "personal", date.today().isoformat(), 600000)
        store.add({"kind":"expense","description":"Mercado demonstração","amount":"25,00",
                   "category":"Mercado","scope":"personal","occurred_on":date.today().isoformat()})
        if "--history" in sys.argv:
            seed_history(store)
        if '--news' in sys.argv:
            seed_history(store)
            store.import_xp_positions({'as_of':date.today().isoformat(),'total_cents':150000,'assets_cents':150000,
                'receivables_cents':0,'cash_cents':0,'assets':[
                    {'name':name,'category':'Ações','value_cents':50000,'cost_cents':45000,'quantity':'5','net_cents':None}
                    for name in ('ABCD11','EFGH11','ZZZZ11')]})
        if "--learning" in sys.argv:
            for amount,category in [('28','Tabaco'),('35','Tabaco'),('150','Transporte'),('180','Transporte'),('32','Importado'),('160','Importado'),('80','Importado')]:
                store.add({'kind':'expense','description':'Autoposto exemplo','amount':amount,
                    'category':category,'occurred_on':date.today().isoformat(),'scope':'personal'})
        if "--origins" in sys.argv:
            with store._connect() as c:
                c.execute('DELETE FROM transactions')
                c.execute('DELETE FROM category_examples')
            today = date.today().isoformat()
            for description,amount,category,kind,excluded,scope in [
                ('Mercado na conta','25','Mercado','expense',False,'personal'),
                ('Débito a classificar','15','Importado','expense',False,'personal'),
                ('Pagamento da fatura','80','Pagamento de fatura','expense',True,'personal'),
                ('Aplicação exemplo','3','Movimento de investimento','expense',True,'personal'),
                ('Receita exemplo','1000','Receita','income',False,'personal'),
                ('Conta da empresa','10','Mercado','expense',False,'business')]:
                store.add({'description':description,'amount':amount,'category':category,'kind':kind,
                    'excluded':excluded,'scope':scope,'occurred_on':today})
            previous = _months(date.today(),1)[0]
            for description,amount,category in [('Mercado no cartão','80','Mercado'),('Transporte no cartão','20','Transporte')]:
                store.add_card_purchase({'bank':'inter','account':'cartão exemplo','scope':'personal',
                    'invoice_month':today[:7],'occurred_on':previous+'-10','description':description,
                    'amount':amount,'category':category})
        with FixtureServer(("127.0.0.1", 0), IdleFixtureHandler if "--idle-auth" in sys.argv else FinanceHandler) as server:
            server.store = store
            server.local_auth = (IdleFixtureAuth(base / "unused.swift", base) if "--idle-auth" in sys.argv else
                                 PasswordFixtureAuth(base / "unused.swift", base)
                                 if "--password-auth" in sys.argv else FixtureAuth())
            server.auth = ChatGPTAuth(base / "auth.json")
            server.cdi = CDIBenchmark(store)
            server.market = MarketQuotes(store, base / "market-token")
            server.pluggy = PluggyXP(store, base / "pluggy.json")
            server.pluggy_inter = PluggyInter(store, server.pluggy)
            server.webhooks = WebhookClient(store, server.pluggy, server.pluggy_inter, base / "relay.json")
            print(server.server_port, flush=True)
            server.serve_forever()


if __name__ == "__main__":
    main()
