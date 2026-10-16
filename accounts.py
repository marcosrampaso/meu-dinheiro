"""Local account registry and independent password/session realms."""
from contextlib import closing, contextmanager
import os
from pathlib import Path
import re
import secrets
import sqlite3
import tempfile
import threading
import time

from local_auth import LocalAuth, LocalAuthError, SESSION_SECONDS


@contextmanager
def data_directory_lock(directory):
    directory.mkdir(parents=True,exist_ok=True,mode=0o700)
    descriptor=os.open(directory/'application.lock',os.O_RDWR|os.O_CREAT|getattr(os,'O_NOFOLLOW',0),0o600)
    try:
        if os.name=='nt':
            import msvcrt
            if not os.fstat(descriptor).st_size: os.write(descriptor,b'0')
            os.lseek(descriptor,0,os.SEEK_SET)
            msvcrt.locking(descriptor,msvcrt.LK_NBLCK,1)
        else:
            import fcntl
            fcntl.flock(descriptor,fcntl.LOCK_EX|fcntl.LOCK_NB)
    except OSError as exc:
        os.close(descriptor)
        raise LocalAuthError('Esta pasta de dados já está aberta em outra instância. Feche o servidor anterior ou escolha outra pasta.') from exc
    try:
        yield
    finally:
        os.close(descriptor)


class PasswordAuth(LocalAuth):
    def _prepare(self):
        raise LocalAuthError('Esta conta usa sua própria senha local.')


def initial_password(auth, password):
    password = auth._password_text(password)
    if auth._identity.get('password'):
        raise LocalAuthError('Esta conta já possui senha.')
    salt = secrets.token_bytes(32)
    identity = {**auth._identity, 'password':{'algorithm':'scrypt-v1','salt':salt.hex(),
                'hash':auth._hash_password(password,salt).hex()}}
    auth._write_identity(identity)
    auth._identity = identity


class LocalAccounts:
    def __init__(self, owner_auth, *, claimable=False):
        self.owner = owner_auth
        self.root = owner_auth.private_dir
        self.path = self.root/'accounts.db'
        self._lock = threading.RLock()
        self._login_lock = threading.Lock()
        self._failures = 0
        self._retry_at = 0
        self._auths = {owner_auth._identity['principal_id']:owner_auth}
        self._demos = {}
        with closing(self._connect()) as c, c:
            c.execute('''CREATE TABLE IF NOT EXISTS accounts (
                principal_id TEXT PRIMARY KEY, username TEXT NOT NULL UNIQUE,
                display_name TEXT NOT NULL, is_owner INTEGER NOT NULL DEFAULT 0,
                claimed INTEGER NOT NULL DEFAULT 1)''')
            existing = c.execute('SELECT principal_id FROM accounts WHERE is_owner=1').fetchone()
            if existing and existing['principal_id'] != owner_auth._identity['principal_id']:
                raise LocalAuthError('O cadastro e a identidade principal não correspondem. Restaure os arquivos do mesmo backup.')
            c.execute('INSERT OR IGNORE INTO accounts VALUES (?,?,?,?,?)',
                (owner_auth._identity['principal_id'],'principal','Minha conta',1,int(not claimable)))
        self.path.chmod(0o600)

    def _connect(self):
        c = sqlite3.connect(self.path)
        c.row_factory = sqlite3.Row
        return c

    @staticmethod
    def username(value):
        if not isinstance(value,str) or not re.fullmatch(r'[a-zA-Z0-9][a-zA-Z0-9_.-]{2,31}',value):
            raise LocalAuthError('Use um usuário com 3 a 32 letras, números, ponto, hífen ou sublinhado.')
        return value.lower()

    @staticmethod
    def display_name(value):
        if not isinstance(value,str) or not 1<=len(value.strip())<=60 or any(ord(ch)<32 for ch in value):
            raise LocalAuthError('Informe um nome de 1 a 60 caracteres.')
        return value.strip()

    def profile(self, principal_id):
        with self._lock:
            if principal_id in self._demos:
                return {'principal_id':principal_id,'username':'demonstracao','display_name':'Demonstração',
                        'is_owner':False,'demo':True}
            with closing(self._connect()) as c:
                row = c.execute('SELECT * FROM accounts WHERE principal_id=?',(principal_id,)).fetchone()
            if not row:
                raise LocalAuthError('Conta local não encontrada.')
            return {key:row[key] for key in ('principal_id','username','display_name','is_owner')} | {'demo':False}

    def directory(self, principal_id):
        profile = self.profile(principal_id)
        if profile['demo']:
            return Path(self._demos[principal_id].name)
        return self.root if profile['is_owner'] else self.root/'accounts'/principal_id

    def _auth(self, principal_id):
        with self._lock:
            if principal_id not in self._auths:
                auth = PasswordAuth(self.owner.source,self.directory(principal_id))
                if auth._identity['principal_id'] != principal_id:
                    raise LocalAuthError('A identidade desta conta não corresponde ao cadastro. Restaure o backup.')
                self._auths[principal_id] = auth
            return self._auths[principal_id]

    def _find(self, token):
        # Only loaded accounts can have sessions in this process.
        with self._lock:
            auths = list(self._auths.values())
        for auth in auths:
            principal = auth.principal(token)
            if principal:
                return auth, principal
        return None, None

    def principal(self, token):
        return self._find(token)[1]

    def valid(self, token):
        return self.principal(token) is not None

    def status(self, token=None):
        auth, principal = self._find(token)
        with closing(self._connect()) as c:
            owner = c.execute('SELECT username,claimed FROM accounts WHERE is_owner=1').fetchone()
        return {**(auth or self.owner).status(),'accounts_enabled':True,'registration_enabled':True,
                'owner_username':owner['username'],'first_registration':not owner['claimed'],
                'profile':self.profile(principal['principal_id']) if principal else None}

    def unlock(self):
        return self.owner.unlock()  # Device biometrics belong only to the original account.

    def unlock_password(self, password, username=None):
        with self._login_lock:
            if time.monotonic()<self._retry_at:
                raise LocalAuthError('Muitas tentativas. Aguarde antes de tentar novamente.')
            try:
                normalized = self.username(username or 'principal')
                with closing(self._connect()) as c:
                    row = c.execute('SELECT principal_id FROM accounts WHERE username=?',(normalized,)).fetchone()
                if row is None:
                    self.owner._hash_password('',bytes(32))
                    raise LocalAuthError('Usuário ou senha inválidos.')
                token = self._auth(row['principal_id']).unlock_password(password)
            except LocalAuthError as exc:
                self._failures += 1
                if self._failures >= 5:
                    self._retry_at = time.monotonic()+min(300,30*2**min(4,self._failures-5))
                raise LocalAuthError('Usuário ou senha inválidos. Confira seus dados ou aguarde antes de tentar novamente.') from exc
            self._failures = 0
            self._retry_at = 0
            return token

    def register(self, username, display_name, password):
        username, display_name = self.username(username), self.display_name(display_name)
        password = self.owner._password_text(password)
        with self._lock, closing(self._connect()) as c, c:
            c.execute('BEGIN IMMEDIATE')
            if c.execute('SELECT 1 FROM accounts WHERE username=? AND claimed=1',(username,)).fetchone():
                raise LocalAuthError('Este usuário já está cadastrado neste dispositivo.')
            owner = c.execute('SELECT * FROM accounts WHERE is_owner=1').fetchone()
            if not owner['claimed']:
                auth = self.owner
                previous = dict(auth._identity)
                try:
                    initial_password(auth,password)
                    c.execute('UPDATE accounts SET username=?,display_name=?,claimed=1 WHERE is_owner=1',(username,display_name))
                    c.commit()
                except Exception:
                    auth._write_identity(previous)
                    auth._identity = previous
                    raise
            else:
                # The path is generated locally; usernames never become directory names.
                identifier = secrets.token_hex(24)
                directory = self.root/'accounts'/identifier
                auth = PasswordAuth(self.owner.source,directory)
                initial_password(auth,password)
                identifier = auth._identity['principal_id']
                # Keep generated identity and directory in agreement before publishing registry entry.
                destination = directory.parent/identifier
                directory.rename(destination)
                auth.private_dir = destination; auth.identity_path = destination/'local-identity.json'
                auth.binary = destination/'touchid-helper'
                c.execute('INSERT INTO accounts VALUES (?,?,?,?,1)',(identifier,username,display_name,0))
                self._auths[identifier] = auth
            return auth._create_session('password')

    def demo(self):
        with self._lock:
            if len(self._demos)>=10:
                raise LocalAuthError('Há dez demonstrações nesta execução. Reinicie o app para limpar as demonstrações temporárias.')
            directory = tempfile.TemporaryDirectory(prefix='meu-dinheiro-demo-')
            auth = PasswordAuth(self.owner.source,Path(directory.name))
            identifier = auth._identity['principal_id']
            self._demos[identifier] = directory
            self._auths[identifier] = auth
            return auth._create_session('demo')

    def session_info(self, token):
        auth, principal = self._find(token)
        info = auth.session_info(token) if auth else None
        return {**info,'profile':self.profile(principal['principal_id'])} if info else None

    def record_activity(self, token, idle_for_ms=0):
        auth, _ = self._find(token)
        return auth.record_activity(token,idle_for_ms) if auth else None

    def configure_password(self, token, password, current_password=None, remove=False):
        auth, principal = self._find(token)
        if not auth or self.profile(principal['principal_id'])['demo']:
            raise LocalAuthError('Entre em uma conta local para configurar uma senha.')
        auth.configure_password(token,password,current_password,remove)
        return self.status(token)

    def lock(self, token):
        auth, _ = self._find(token)
        if auth:
            auth.lock(token)

    def close(self):
        for directory in self._demos.values():
            directory.cleanup()
