"""Consume hosted notices while the local financial app is unlocked."""

from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import secrets
import tempfile
import threading
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import Request, build_opener, HTTPRedirectHandler

from finance import ValidationError
from pluggy_xp import PluggyError, UUID, _http_json

EVENTS = ('item/updated','item/error','item/waiting_user_input','item/waiting_user_action','item/deleted')
TOKEN = re.compile(r'[A-Za-z0-9_-]{32,128}\Z')


class RelayError(Exception):
    pass


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def relay_request(base_url, token, method, path, payload=None):
    body = json.dumps(payload).encode() if payload is not None else None
    request = Request(base_url+path, data=body, method=method,
                      headers={'Authorization':'Bearer '+token,'Accept':'application/json',
                               'Content-Type':'application/json'})
    try:
        with build_opener(NoRedirect).open(request, timeout=15) as response:
            raw = response.read(131073)
            if len(raw)>131072:
                raise RelayError('O receptor devolveu uma resposta grande demais.')
            result = json.loads(raw)
    except HTTPError as exc:
        raise RelayError(f'O receptor recusou a consulta (HTTP {exc.code}). Confira a URL e a chave.') from None
    except (URLError, OSError, ValueError):
        raise RelayError('Não foi possível consultar o receptor agora.') from None
    if not isinstance(result,dict):
        raise RelayError('Resposta inválida do receptor.')
    return result


class WebhookClient:
    def __init__(self, store, pluggy, inter, config_path: Path):
        self.store, self.pluggy, self.inter = store, pluggy, inter
        self.path = config_path
        self._lock = threading.Lock()
        with store._connect() as c:
            c.execute('''CREATE TABLE IF NOT EXISTS webhook_receipts (
                event_id TEXT PRIMARY KEY, item_id TEXT NOT NULL, event TEXT NOT NULL,
                source TEXT NOT NULL, notice TEXT NOT NULL, processed_at TEXT NOT NULL)''')

    def load_config(self):
        try:
            config = json.loads(self.path.read_text())
        except (OSError,ValueError):
            return {}
        return config if isinstance(config,dict) else {}

    def _write(self, config):
        self.path.parent.mkdir(parents=True,exist_ok=True)
        fd, temporary = tempfile.mkstemp(prefix='.webhook-',dir=self.path.parent)
        try:
            os.fchmod(fd,0o600)
            with os.fdopen(fd,'w') as output:
                json.dump(config,output)
            os.replace(temporary,self.path)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)

    def generate_setup(self):
        config = self.pluggy.load_config()
        item_ids = [config[k].lower() for k in ('item_id','inter_item_id') if config.get(k)]
        if not item_ids or any(not UUID.fullmatch(item) for item in item_ids):
            raise ValidationError('Configure XP ou Inter antes de gerar as chaves do receptor.')
        local = self.load_config()
        local.update(ingest_token=secrets.token_urlsafe(32),read_token=secrets.token_urlsafe(32),enabled=False)
        self._write(local)
        # Explicit setup action only. GET status never exposes these values.
        return {'variables':{'RELAY_INGEST_TOKEN':local['ingest_token'],
                             'RELAY_READ_TOKEN':local['read_token'],
                             'RELAY_ITEM_IDS':','.join(item_ids)},
                'note':'Copie para as Variables do receptor na Railway. Gerar novamente exige atualizar as chaves também na Railway e na Pluggy.'}

    def save_config(self, data):
        if not isinstance(data,dict):
            raise ValidationError('Configuração do receptor inválida.')
        config = self.load_config()
        url = data.get('url','')
        token = data.get('read_token','')
        enabled = data.get('enabled',False)
        if not isinstance(url,str) or not isinstance(token,str) or not isinstance(enabled,bool):
            raise ValidationError('Informe URL, chave e opção de sincronização válidas.')
        parsed = urlsplit(url.strip())
        if parsed.scheme!='https' or not parsed.hostname or not parsed.hostname.endswith('.up.railway.app') or \
                parsed.username or parsed.password or parsed.netloc!=parsed.hostname or parsed.path not in ('','/') or parsed.query or parsed.fragment:
            raise ValidationError('Use a URL HTTPS pública da Railway, terminada em .up.railway.app, sem caminhos.')
        token = token.strip() or config.get('read_token','')
        if not TOKEN.fullmatch(token):
            raise ValidationError('Informe a chave de leitura do receptor ou gere as chaves locais.')
        self._write({**config,'url':url.strip().rstrip('/'),'read_token':token,'enabled':enabled})

    def status(self):
        config = self.load_config()
        with self.store._connect() as c:
            notices = [dict(r) for r in c.execute('SELECT source,event,notice,processed_at FROM webhook_receipts ORDER BY processed_at DESC,rowid DESC LIMIT 10')]
        return {'configured':bool(config.get('url') and config.get('read_token')),
                'keys_saved':bool(config.get('ingest_token')),'url':config.get('url',''),
                'enabled':bool(config.get('enabled')),'notices':notices}

    def delete_config(self):
        self.path.unlink(missing_ok=True)

    def register(self, http_json=_http_json):
        config = self.load_config()
        if not config.get('url') or not config.get('ingest_token'):
            raise ValidationError('Gere as chaves e salve a URL do receptor antes de registrar os webhooks.')
        relay_request(config['url'],config['read_token'],'GET','/events')
        pluggy = self.pluggy.load_config()
        auth = http_json('POST','/auth',{'clientId':pluggy['client_id'],'clientSecret':pluggy['client_secret']})
        api_key = auth.get('apiKey')
        if not isinstance(api_key,str) or not api_key:
            raise PluggyError('A Pluggy não devolveu uma chave válida.')
        from pluggy_inter import _pages
        rows = _pages('/webhooks',api_key,http_json)
        target = config['url']+'/webhooks/pluggy'
        for event in EVENTS:
            matching = [r for r in rows if r.get('url')==target and r.get('event')==event]
            payload = {'url':target,'event':event,'headers':{'X-Webhook-Key':config['ingest_token']}}
            if matching:
                if matching[0].get('disabledAt'):
                    raise PluggyError('Há um webhook desativado para essa URL. Confira a ativação no Dashboard da Pluggy.')
                identifier = matching[0].get('id')
                if not isinstance(identifier,str) or not UUID.fullmatch(identifier):
                    raise PluggyError('Um webhook existente veio sem ID válido.')
                http_json('PATCH','/webhooks/'+identifier,payload,api_key=api_key)
            else:
                http_json('POST','/webhooks',payload,api_key=api_key)
        return {'registered':len(EVENTS),'url':target}

    def check(self, transport=relay_request, *, manual=False):
        if not isinstance(manual, bool):
            raise ValidationError('A opção de conferência manual deve ser verdadeira ou falsa.')
        config = self.load_config()
        if not config.get('url') or not config.get('read_token') or (not manual and not config.get('enabled')):
            return {'synchronized':[],'processed':0,'message':'Sincronização por avisos desativada.'}
        if not self._lock.acquire(blocking=False):
            return {'synchronized':[],'processed':0,'message':'Uma sincronização já está em andamento.'}
        try:
            return self._check(config,transport)
        finally:
            self._lock.release()

    def _synchronize(self, source):
        from pluggy_xp import PluggyXP
        from pluggy_inter import PluggyInter
        pluggy = PluggyXP(self.store,self.pluggy.config_path)
        integration = pluggy if source=='XP' else PluggyInter(self.store,pluggy)
        preview = integration.preview_sync() if source=='XP' else integration.preview()
        return integration.import_preview(preview['preview_token'])

    def _check(self, config, transport):
        response = transport(config['url'],config['read_token'],'GET','/events')
        rows = response.get('events')
        if not isinstance(rows,list) or len(rows)>100:
            raise RelayError('O receptor devolveu uma lista inválida de avisos.')
        for r in rows:
            if not isinstance(r,dict) or any(not isinstance(r.get(k),str) or not UUID.fullmatch(r[k]) for k in ('event_id','item_id')) or r.get('event') not in EVENTS:
                raise RelayError('O receptor devolveu um aviso inválido.')
        if not rows:
            return {'synchronized':[],'processed':0,'message':'Nenhum aviso novo.'}
        pluggy = self.pluggy.load_config()
        sources = {pluggy.get('item_id','').lower():'XP',pluggy.get('inter_item_id','').lower():'Inter'}
        with self.store._connect() as c:
            done = {r[0] for r in c.execute('SELECT event_id FROM webhook_receipts')}
        pending = [r for r in rows if r['event_id'] not in done]
        synced, successful, errors = [], [], []
        for item_id in dict.fromkeys(r['item_id'] for r in pending):
            group = [r for r in pending if r['item_id']==item_id]
            source = sources.get(item_id,'Conexão desconhecida')
            try:
                # Coalesce notices, then fetch the current canonical provider data once.
                if source in ('XP','Inter') and group[-1]['event']=='item/updated':
                    self._synchronize(source)
                    synced.append(source)
                stamp = datetime.now(timezone.utc).isoformat(timespec='seconds')
                with self.store._connect() as c:
                    for row in group:
                        notice = {'item/updated':'Dados atualizados pela Pluggy.',
                                  'item/error':'A conexão precisa de atenção no Meu Pluggy.',
                                  'item/waiting_user_input':'Confirme a autenticação pedida no Meu Pluggy.',
                                  'item/waiting_user_action':'Autorize a ação pedida pelo banco no Meu Pluggy.',
                                  'item/deleted':'Conexão removida na Pluggy; reconecte para continuar.'}[row['event']]
                        if source=='Conexão desconhecida':
                            notice='Aviso de uma conexão que não está configurada neste app; nenhuma conta foi sincronizada.'
                        c.execute('INSERT OR IGNORE INTO webhook_receipts VALUES (?,?,?,?,?,?)',
                                  (row['event_id'],item_id,row['event'],source,notice,stamp))
                        successful.append(row['event_id'])
            except PluggyError as exc:
                errors.append({'source':source,'message':str(exc)})
        ack = [r['event_id'] for r in rows if r['event_id'] in done or r['event_id'] in successful]
        ack_failed = False
        if ack:
            try:
                transport(config['url'],config['read_token'],'POST','/events/ack',{'event_ids':ack})
            except RelayError:
                ack_failed = True
        return {'synchronized':synced,'processed':len(successful),'errors':errors,
                'ack_pending':ack_failed,'has_more':bool(response.get('has_more')),
                'message':'Avisos conferidos.' if not errors else 'Algumas conexões precisam de atenção.'}
