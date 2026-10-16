import tempfile
import unittest
import threading
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from accounts import LocalAccounts, PasswordAuth
from app import FinanceHTTPServer
from finance import FinanceStore
from local_auth import LocalAuthError
from workspaces import Workspace
from tests import test_api as api_fixtures


PASSWORD='a sufficiently long test password'


class AccountTests(unittest.TestCase):
    def setUp(self):
        directory=tempfile.TemporaryDirectory(); self.addCleanup(directory.cleanup)
        self.root=Path(directory.name)
        self.owner=PasswordAuth(self.root/'unused.swift',self.root)
        self.accounts=LocalAccounts(self.owner)
        self.addCleanup(self.accounts.close)

    def test_password_login_registration_restart_and_no_clear_password(self):
        token=self.accounts.register('Alice','Alice exemplo',PASSWORD)
        principal=self.accounts.principal(token)['principal_id']
        folder=self.accounts.directory(principal)
        self.assertNotEqual(folder,self.root)
        self.assertNotIn(PASSWORD,(folder/'local-identity.json').read_text())
        self.assertEqual((folder/'local-identity.json').stat().st_mode & 0o777,0o600)
        with self.assertRaises(LocalAuthError): self.accounts.unlock_password('incorrect','alice')
        restored=LocalAccounts(PasswordAuth(self.root/'unused.swift',self.root))
        self.addCleanup(restored.close)
        again=restored.unlock_password(PASSWORD,'ALICE')
        self.assertEqual(restored.principal(again)['principal_id'],principal)
        with self.assertRaises(LocalAuthError): restored.register('alice','Outro',PASSWORD)
        for value in ('../owner','a/b','ab','a b'):
            with self.assertRaises(LocalAuthError): self.accounts.register(value,'Teste',PASSWORD)

    def test_fresh_install_claims_first_account_without_moving_data(self):
        self.accounts=LocalAccounts(self.owner,claimable=True)
        # Existing registry belongs to a migrated user; it cannot be claimed.
        self.assertFalse(self.accounts.status()['first_registration'])
        fresh=self.root/'fresh'
        auth=PasswordAuth(fresh/'unused.swift',fresh)
        accounts=LocalAccounts(auth,claimable=True); self.addCleanup(accounts.close)
        token=accounts.register('pessoa','Primeiro usuário',PASSWORD)
        self.assertEqual(accounts.directory(accounts.principal(token)['principal_id']),fresh)
        self.assertFalse(accounts.status()['first_registration'])
        self.assertEqual(accounts.status(token)['profile']['username'],'pessoa')

    def test_sessions_password_changes_and_demo_are_separate(self):
        a=self.accounts.register('alice','Alice',PASSWORD)
        b=self.accounts.register('bruno','Bruno',PASSWORD)
        first=self.accounts.principal(a)['principal_id']
        self.accounts.configure_password(a,PASSWORD+' changed',PASSWORD)
        self.assertTrue(self.accounts.valid(b))
        self.accounts.lock(a)
        self.assertFalse(self.accounts.valid(a)); self.assertTrue(self.accounts.valid(b))
        with self.assertRaises(LocalAuthError): self.accounts.unlock()
        d=self.accounts.demo()
        self.assertTrue(self.accounts.status(d)['profile']['demo'])
        self.assertNotEqual(self.accounts.principal(d)['principal_id'],first)
        with self.assertRaises(LocalAuthError): self.accounts.configure_password(d,PASSWORD)


class AccountApiTests(unittest.TestCase):
    call_api=api_fixtures.ApiTests.call_api

    def setUp(self):
        api_fixtures.ApiTests.setUp(self); self.addCleanup(self.directory.cleanup)
        directory=Path(self.directory.name)
        manager=LocalAccounts(PasswordAuth(directory/'unused.swift',directory))
        self.addCleanup(manager.close)
        self.server.local_auth=manager
        self.server._workspace_lock=threading.RLock()
        owner_id=manager.owner._identity['principal_id']
        self.server._workspaces={owner_id:Workspace(self.store,manager.profile(owner_id),background=False)}
        self.server._background=False
        self.server.for_principal=lambda identifier: FinanceHTTPServer.for_principal(self.server,identifier)
        self.server.for_oauth=lambda state: FinanceHTTPServer.for_oauth(self.server,state)
        self._starts=patch('workspaces.MarketQuotes.start'); self._starts.start();self.addCleanup(self._starts.stop)
        self._cdi=patch('workspaces.CDIBenchmark.start');self._cdi.start();self.addCleanup(self._cdi.stop)

    def register(self, username):
        status,data=self.call_api('/api/v1/local-auth/register','POST',{
            'username':username,'display_name':username,'password':PASSWORD,'password_confirmation':PASSWORD},authenticated=False)
        self.assertEqual(status,201)
        return data['token'],data['profile']['principal_id']

    def request(self,path,token,method='GET',data=None):
        return self.call_api(path,method,data,headers={'X-Local-Session':token},authenticated=False)

    def test_requests_and_colliding_transaction_ids_are_bound_to_the_session(self):
        a,aid=self.register('alice'); b,bid=self.register('bruno')
        payload={'kind':'expense','description':'Conta A somente','category':'Mercado','amount':'12','occurred_on':'2026-10-15'}
        _,created=self.request('/api/v1/transactions',a,'POST',payload)
        self.assertEqual(self.request('/api/v1/transactions/'+str(created['id']),b)[0],404)
        self.assertEqual(self.request('/api/v1/transactions?month=2026-10&principal_id='+aid,b)[1]['items'],[])
        other={**payload,'description':'Conta B somente','amount':'33'}
        _,second=self.request('/api/v1/transactions',b,'POST',other)
        self.assertEqual(created['id'],second['id'])
        self.assertEqual(self.request('/api/v1/transactions/'+str(created['id']),a)[1]['description'],'Conta A somente')
        self.assertEqual(self.request('/api/v1/transactions/'+str(second['id']),b)[1]['description'],'Conta B somente')
        wa=self.server.for_principal(aid);wb=self.server.for_principal(bid)
        self.assertNotEqual(wa.auth.path,wb.auth.path)
        self.assertNotEqual(wa.pluggy.config_path if hasattr(wa.pluggy,'config_path') else wa.store.path,
                            wb.pluggy.config_path if hasattr(wb.pluggy,'config_path') else wb.store.path)
        self.assertEqual(self.server.store.path,self.store.path)

    def test_oauth_callback_uses_the_pending_state_not_the_last_logged_in_user(self):
        a,aid=self.register('alice'); b,bid=self.register('bruno')
        wa=self.server.for_principal(aid); wb=self.server.for_principal(bid)
        wb.auth.pending['unique-state']={}
        with patch.object(wb.auth,'complete') as right,patch.object(wa.auth,'complete') as wrong:
            self.assertEqual(self.request('/auth/callback?state=unique-state',a)[0],303)
            right.assert_called_once(); wrong.assert_not_called()
        self.assertEqual(self.request('/auth/callback?state=unknown',a)[0],403)

    def test_demo_cannot_connect_external_services_and_does_not_read_owner_data(self):
        self.store.add({'kind':'expense','description':'PRIVATE-OWNER-RECORD','amount':'12','category':'Mercado','occurred_on':'2026-10-15'})
        status,data=self.call_api('/api/v1/local-auth/demo','POST',{},authenticated=False)
        self.assertEqual(status,201); token=data['token']
        status,transactions=self.request('/api/v1/transactions?month=2026-10',token)
        self.assertEqual(status,200);self.assertNotIn('PRIVATE-OWNER-RECORD',str(transactions))
        for path in ('/auth/chatgpt/start','/investments/pluggy/config','/pluggy/webhooks/check','/investments/news','/investments/brapi-token'):
            self.assertEqual(self.request('/api/v1'+path,token,'POST',{})[0],400)
        self.assertEqual(self.request('/api/v1/investments/news',token)[0],200)
        self.assertIsNotNone(self.request('/api/v1/investments/news',token)[1]['report'])
        self.assertEqual(self.call_api('/api/v1/local-auth/register','POST',{'username':'evil'},
            authenticated=False,headers={'Origin':'https://unrelated.example'})[0],403)
