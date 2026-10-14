from concurrent.futures import ThreadPoolExecutor
import os
from pathlib import Path
import tempfile
import unittest
from uuid import uuid4

from fastapi.testclient import TestClient
from relay.app import create_app, Settings
from relay.store import EventStore, StoreError

ITEM = '11111111-1111-1111-1111-111111111111'
INGEST = 'i'*43
READ = 'r'*43


class RelayTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.store = EventStore('sqlite:///'+str(Path(self.directory.name)/'relay.db'),allow_sqlite=True)
        self.app = create_app(Settings(INGEST,READ,frozenset({ITEM})),self.store)
        self.client = self.enterContext(TestClient(self.app))
        self.event = {'event':'item/updated','eventId':str(uuid4()),'itemId':ITEM,'triggeredBy':'SYNC'}

    def post(self, payload=None, token=INGEST):
        return self.client.post('/webhooks/pluggy',json=payload or self.event,headers={'X-Webhook-Key':token.encode()})

    def pending(self):
        return self.client.get('/events',headers={'Authorization':'Bearer '+READ})

    def test_authentication_roles_and_item_allowlist(self):
        self.assertEqual(self.post(token=READ).status_code,401)
        self.assertEqual(self.post(token='ç').status_code,401)
        self.assertEqual(self.client.get('/events').status_code,401)
        self.assertEqual(self.client.get('/events',headers={'Authorization':'Bearer '+INGEST}).status_code,401)
        self.assertEqual(self.post({**self.event,'itemId':str(uuid4())}).status_code,403)
        self.assertEqual(self.pending().json()['events'],[])

    def test_duplicate_delivery_ack_and_later_replay(self):
        self.assertEqual(self.post().status_code,202)
        self.assertTrue(self.post().json()['duplicate'])
        self.assertEqual(len(self.pending().json()['events']),1)
        response = self.client.post('/events/ack',json={'event_ids':[self.event['eventId']]},headers={'Authorization':'Bearer '+READ})
        self.assertEqual(response.json()['acknowledged'],1)
        self.assertEqual(self.pending().json()['events'],[])
        self.assertTrue(self.post().json()['duplicate'])
        self.assertEqual(self.pending().json()['events'],[])

    def test_payload_is_minimized_and_size_limited(self):
        self.post({**self.event,'clientUserId':'private-person','accountNumber':'private-account',
                   'amount':999,'createdTransactionsLink':'https://private.invalid/file'})
        result = self.pending()
        self.assertNotIn('private',result.text)
        self.assertNotIn('999',result.text)
        self.assertEqual(result.headers['cache-control'],'no-store')
        self.assertEqual(self.post({**self.event,'extra':'x'*33000}).status_code,413)
        self.assertEqual(self.post({**self.event,'event':[]}).status_code,400)

    def test_commit_failure_is_not_acknowledged(self):
        def failing(event):
            raise StoreError('private database message')
        self.store.enqueue = failing
        response = self.post()
        self.assertEqual(response.status_code,503)
        self.assertNotIn('private',response.text)
        self.assertEqual(self.pending().json()['events'],[])

    def test_restart_keeps_pending_events(self):
        self.post()
        restarted = EventStore(self.store.url,allow_sqlite=True)
        restarted.initialize()
        self.assertEqual(restarted.pending()['events'][0]['event_id'],self.event['eventId'])


@unittest.skipUnless(os.environ.get('WEBHOOK_TEST_DATABASE_URL'),'Requires an isolated PostgreSQL test database')
class PostgresRelayTests(RelayTests):
    def setUp(self):
        from urllib.parse import urlsplit
        url = os.environ['WEBHOOK_TEST_DATABASE_URL']
        parsed = urlsplit(url)
        assert parsed.hostname in ('localhost','127.0.0.1') and parsed.path=='/financas_webhook_test'
        self.store = EventStore(url)
        self.store.initialize()
        with self.store.connect() as c:
            c.execute('TRUNCATE relay_events')
        self.app = create_app(Settings(INGEST,READ,frozenset({ITEM})),self.store)
        self.client = self.enterContext(TestClient(self.app))
        self.event = {'event':'item/updated','eventId':str(uuid4()),'itemId':ITEM,'triggeredBy':'SYNC'}

    def test_restart_keeps_pending_events(self):
        self.post()
        restarted = EventStore(self.store.url)
        restarted.initialize()
        self.assertEqual(restarted.pending()['events'][0]['event_id'],self.event['eventId'])

    def test_simultaneous_deliveries_persist_one_event(self):
        event = {'event_id':self.event['eventId'],'item_id':ITEM,'event':'item/updated','triggered_by':'SYNC'}
        with ThreadPoolExecutor(max_workers=8) as pool:
            results = list(pool.map(lambda _:self.store.enqueue(event),range(20)))
        self.assertEqual(sum(results),1)
        self.assertEqual(len(self.store.pending()['events']),1)


if __name__=='__main__':
    unittest.main()
