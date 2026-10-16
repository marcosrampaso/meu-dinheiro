"""Full account routing on a temporary server, with native/network integrations disabled."""
from pathlib import Path
import signal
import sys
import tempfile
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from app import FinanceHTTPServer
from accounts import PasswordAuth
from finance import FinanceStore


def stop(signum,frame): raise KeyboardInterrupt
signal.signal(signal.SIGTERM,stop)
with tempfile.TemporaryDirectory(prefix='finance-accounts-fixture-') as folder:
    with patch('app.LocalAuth',PasswordAuth),patch('workspaces.MarketQuotes.start'),patch('workspaces.CDIBenchmark.start'):
        with FinanceHTTPServer(('127.0.0.1',0),FinanceStore(Path(folder)/'financas.db')) as server:
            print(server.server_port,flush=True)
            try: server.serve_forever()
            except KeyboardInterrupt: pass
