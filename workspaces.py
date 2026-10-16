"""Bind each HTTP request to its authenticated account without shared mutable routing."""
from chatgpt_auth import ChatGPTAuth
from cdi import CDIBenchmark
from market_quotes import MarketQuotes
from pluggy_xp import PluggyXP
from pluggy_inter import PluggyInter
from webhook_client import WebhookClient


class Workspace:
    def __init__(self, store, profile, *, background=True):
        self.store, self.profile, self.demo = store, profile, profile.get('demo',False)
        directory = store.path.parent
        self.auth = ChatGPTAuth(directory/'chatgpt-auth.json')
        self.market = MarketQuotes(store,directory/'brapi-token')
        self.pluggy = PluggyXP(store,directory/'pluggy-xp.json')
        self.pluggy_inter = PluggyInter(store,self.pluggy)
        self.webhooks = WebhookClient(store,self.pluggy,self.pluggy_inter,directory/'pluggy-relay.json')
        self.cdi = CDIBenchmark(store)
        if background and not self.demo:
            self.market.start(); self.cdi.start()


class RequestWorkspace:
    def __init__(self, server, workspace):
        self._server, self._workspace = server, workspace

    def __getattr__(self, name):
        return getattr(self._workspace,name) if hasattr(self._workspace,name) else getattr(self._server,name)
