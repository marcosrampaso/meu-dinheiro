"""Receive Pluggy notices; financial credentials and records stay on the Mac."""

from contextlib import asynccontextmanager
from dataclasses import dataclass
import json
import os
import re
import secrets
from uuid import UUID

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
from starlette.concurrency import run_in_threadpool

from .store import EventStore, StoreError, EventConflict

EVENTS = frozenset({'item/updated','item/error','item/waiting_user_input',
                    'item/waiting_user_action','item/deleted'})
MAX_BODY = 32768
TOKEN = re.compile(r'[A-Za-z0-9_-]{32,128}\Z')


@dataclass(frozen=True)
class Settings:
    ingest_token: str
    read_token: str
    item_ids: frozenset

    def __post_init__(self):
        if not TOKEN.fullmatch(self.ingest_token) or not TOKEN.fullmatch(self.read_token) or self.ingest_token == self.read_token or \
                self.ingest_token.startswith('GENERATE_') or self.read_token.startswith('GENERATE_'):
            raise ValueError('Configure duas chaves distintas, aleatórias, com pelo menos 32 caracteres.')
        if not self.item_ids or any(str(UUID(item)) != item for item in self.item_ids):
            raise ValueError('RELAY_ITEM_IDS deve conter os itemIds autorizados.')

    @classmethod
    def from_env(cls):
        return cls(os.getenv('RELAY_INGEST_TOKEN',''), os.getenv('RELAY_READ_TOKEN',''),
                   frozenset(str(UUID(x.strip())) for x in os.getenv('RELAY_ITEM_IDS','').split(',') if x.strip()))


def require_token(actual, expected):
    if not isinstance(actual,str) or not secrets.compare_digest(actual.encode(),expected.encode()):
        raise HTTPException(401, 'Chave inválida.')


def uuid_string(value):
    if not isinstance(value,str):
        raise HTTPException(400,'Identificador inválido.')
    try:
        return str(UUID(value))
    except ValueError:
        raise HTTPException(400,'Identificador inválido.') from None


async def read_json(request):
    if request.headers.get('content-type','').split(';')[0] != 'application/json':
        raise HTTPException(415,'Envie JSON.')
    body = bytearray()
    async for chunk in request.stream():
        body.extend(chunk)
        if len(body)>MAX_BODY:
            raise HTTPException(413,'Corpo muito grande.')
    try:
        payload = json.loads(body)
    except (ValueError,UnicodeError):
        raise HTTPException(400,'JSON inválido.') from None
    if not isinstance(payload,dict):
        raise HTTPException(400,'Envie um objeto JSON.')
    return payload


def create_app(settings=None, store=None):
    @asynccontextmanager
    async def lifespan(app):
        app.state.settings = settings or Settings.from_env()
        app.state.store = store or EventStore(os.getenv('DATABASE_URL',''))
        try:
            await run_in_threadpool(app.state.store.initialize)
        except StoreError:
            raise RuntimeError('Não foi possível iniciar o banco do receptor.') from None
        yield

    app = FastAPI(lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)

    @app.middleware('http')
    async def private_responses(request, call_next):
        response = await call_next(request)
        response.headers['Cache-Control'] = 'no-store'
        response.headers['X-Content-Type-Options'] = 'nosniff'
        return response

    @app.exception_handler(StoreError)
    async def unavailable(request, exc):
        return JSONResponse({'detail':'Banco indisponível. Tente novamente.'},status_code=503)

    @app.exception_handler(EventConflict)
    async def conflict(request, exc):
        return JSONResponse({'detail':'eventId em conflito.'},status_code=409)

    @app.get('/health')
    def health():
        app.state.store.health()
        return {'status':'ok'}

    @app.post('/webhooks/pluggy',status_code=202)
    async def receive(request: Request):
        require_token(request.headers.get('X-Webhook-Key'),app.state.settings.ingest_token)
        payload = await read_json(request)
        if not isinstance(payload.get('event'), str) or payload['event'] not in EVENTS:
            raise HTTPException(400,'Evento não suportado.')
        item_id = uuid_string(payload.get('itemId'))
        if item_id not in app.state.settings.item_ids:
            raise HTTPException(403,'Conexão não autorizada.')
        triggered = payload.get('triggeredBy')
        if triggered not in (None,'USER','CLIENT','SYNC','INTERNAL'):
            raise HTTPException(400,'Origem do evento inválida.')
        # Allowlist these four fields; do not persist the complete provider payload.
        event = {'event_id':uuid_string(payload.get('eventId')), 'item_id':item_id,
                 'event':payload['event'],'triggered_by':triggered}
        inserted = await run_in_threadpool(app.state.store.enqueue,event)
        return {'accepted':True,'duplicate':not inserted}

    @app.get('/events')
    def events(request: Request):
        require_token(request.headers.get('Authorization'), 'Bearer '+app.state.settings.read_token)
        return app.state.store.pending()

    @app.post('/events/ack')
    async def acknowledge(request: Request):
        require_token(request.headers.get('Authorization'), 'Bearer '+app.state.settings.read_token)
        payload = await read_json(request)
        event_ids = payload.get('event_ids')
        if not isinstance(event_ids,list) or not 1<=len(event_ids)<=100:
            raise HTTPException(400,'Envie de 1 a 100 eventIds.')
        event_ids = list({uuid_string(value) for value in event_ids})
        count = await run_in_threadpool(app.state.store.acknowledge,event_ids)
        return {'acknowledged':count}

    return app


app = create_app()
