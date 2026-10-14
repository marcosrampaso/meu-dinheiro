import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from uuid import uuid4

from finance import FinanceStore, ValidationError
from pluggy_xp import PluggyXP, PluggyError
from pluggy_inter import PluggyInter
from webhook_client import WebhookClient, RelayError

XP = '11111111-1111-1111-1111-111111111111'
INTER = '22222222-2222-2222-2222-222222222222'


class WebhookClientTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        base = Path(self.directory.name)
        self.store = FinanceStore(base/'test.db')
        self.pluggy = PluggyXP(self.store,base/'pluggy.json')
        self.pluggy.save_config({'client_id':'client','client_secret':'secret','item_id':XP,'inter_item_id':INTER})
        self.client = WebhookClient(self.store,self.pluggy,PluggyInter(self.store,self.pluggy),base/'relay.json')
        self.client.generate_setup()
        self.client.save_config({'url':'https://example.up.railway.app','enabled':True})
        self.events=[]
        self.acked=[]

    def event(self,item=XP,event='item/updated'):
        return {'event_id':str(uuid4()),'item_id':item,'event':event}

    def transport(self,url,token,method,path,payload=None):
        if method=='GET':
            return {'events':self.events,'has_more':False}
        self.acked.extend(payload['event_ids'])
        return {'acknowledged':len(payload['event_ids'])}

    def test_status_does_not_return_keys_and_config_is_private(self):
        status=json.dumps(self.client.status())
        for key in ('read_token','ingest_token'):
            self.assertNotIn(self.client.load_config()[key],status)
        self.assertEqual(os.stat(self.client.path).st_mode & 0o777,0o600)
        for url in ('http://example.up.railway.app','https://example.up.railway.app.evil.test',
                    'https://127.0.0.1','https://example.up.railway.app/secret','https://user@example.up.railway.app'):
            with self.assertRaises(ValidationError):self.client.save_config({'url':url})

    def test_coalesces_item_events_and_ack_failure_does_not_resync(self):
        self.events=[self.event(),self.event(),self.event(INTER)]
        def ack_failure(url,token,method,path,payload=None):
            if method=='POST':raise RelayError('offline')
            return self.transport(url,token,method,path,payload)
        with patch.object(self.client,'_synchronize') as sync:
            result=self.client.check(ack_failure)
            self.assertEqual(result['synchronized'],['XP','Inter'])
            self.assertTrue(result['ack_pending'])
            self.assertEqual(sync.call_count,2)
            again=self.client.check(self.transport)
            self.assertEqual(sync.call_count,2)
            self.assertEqual(again['processed'],0)
            self.assertEqual(len(self.acked),3)

    def test_failed_sync_remains_pending(self):
        self.events=[self.event()]
        with patch.object(self.client,'_synchronize',side_effect=PluggyError('try again')):
            result=self.client.check(self.transport)
        self.assertTrue(result['errors'])
        self.assertEqual(self.acked,[])
        self.assertEqual(self.client.status()['notices'],[])

    def test_manual_check_works_while_automatic_sync_stays_paused(self):
        self.client.save_config({'url':'https://example.up.railway.app','enabled':False})
        self.events=[self.event()]
        with patch.object(self.client,'_synchronize') as sync:
            with patch('webhook_client.relay_request') as transport:
                self.assertEqual(self.client.check(transport)['processed'],0)
                transport.assert_not_called()
            result=self.client.check(self.transport,manual=True)
            sync.assert_called_once_with('XP')
        self.assertEqual(result['synchronized'],['XP'])
        self.assertEqual(self.acked,[self.events[0]['event_id']])
        self.assertFalse(self.client.status()['enabled'])
        with self.assertRaises(ValidationError):
            self.client.check(self.transport,manual='false')

    def test_authentication_and_deletion_notices_do_not_modify_finances(self):
        self.events=[self.event(event='item/waiting_user_input'),self.event(INTER,'item/deleted'),self.event(str(uuid4()))]
        with patch.object(self.client,'_synchronize') as sync:
            result=self.client.check(self.transport)
        sync.assert_not_called()
        self.assertEqual(result['processed'],3)
        self.assertEqual(len(self.acked),3)

    def test_last_failure_suppresses_old_update_notice(self):
        self.events=[self.event(),self.event(event='item/error')]
        with patch.object(self.client,'_synchronize') as sync:
            self.client.check(self.transport)
        sync.assert_not_called()

    def test_registration_reuses_existing_hooks_without_exposing_headers(self):
        target='https://example.up.railway.app/webhooks/pluggy'
        calls=[]
        existing={'id':str(uuid4()),'url':target,'event':'item/updated','headers':{'X-Webhook-Key':'old'}}
        def http(method,path,payload=None,api_key=None):
            calls.append((method,path,payload))
            if path=='/auth':return {'apiKey':'session'}
            if method=='GET':return {'results':[existing],'totalPages':1}
            return {'id':str(uuid4()),**(payload or {})}
        with patch('webhook_client.relay_request',return_value={'events':[]}):
            result=self.client.register(http)
        self.assertEqual(result['registered'],5)
        self.assertEqual(sum(method=='PATCH' for method,path,payload in calls),1)
        self.assertEqual(sum(method=='POST' and path=='/webhooks' for method,path,payload in calls),4)
        self.assertNotIn(self.client.load_config()['ingest_token'],json.dumps(result))


if __name__=='__main__':unittest.main()
