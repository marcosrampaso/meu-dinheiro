"""Isolated UI fixture. Fake authentication and synthetic financial records only."""

from datetime import date
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


class FixtureAuth:
    def valid(self, token):
        return token == "synthetic-ui-session"

    def unlock(self):
        return "synthetic-ui-session"

    def lock(self, token):
        pass


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
        with ThreadingHTTPServer(("127.0.0.1", 0), FinanceHandler) as server:
            server.store = store
            server.local_auth = FixtureAuth()
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
