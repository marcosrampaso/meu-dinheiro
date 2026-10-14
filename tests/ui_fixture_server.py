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


class FixtureServer(ThreadingHTTPServer):
    request_queue_size = 64


class PasswordFixtureAuth(LocalAuth):
    """Exercise real password/session logic, with no native biometric calls."""
    def _prepare(self):
        pass

    def unlock(self):
        return self._create_session("touch_id")


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
        with FixtureServer(("127.0.0.1", 0), FinanceHandler) as server:
            server.store = store
            server.local_auth = (PasswordFixtureAuth(base / "unused.swift", base)
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
