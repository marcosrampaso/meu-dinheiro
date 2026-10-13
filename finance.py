"""Storage and validation for a small personal finance application."""

from __future__ import annotations

import sqlite3
import os
import re
import unicodedata
import hashlib
import json
from contextlib import closing, contextmanager
from datetime import date
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from pathlib import Path


class ValidationError(ValueError):
    """Raised when a transaction contains invalid user input."""


class NotFoundError(LookupError):
    """Raised when a transaction ID does not exist."""


PURCHASE_CATEGORIES = (
    "Alimentação", "Mercado", "Moradia", "Transporte", "Saúde", "Educação",
    "Lazer", "Compras", "Assinaturas", "Viagens", "Cuidados pessoais",
    "Impostos e taxas", "Serviços", "Outros",
)
CARD_BANKS = {"inter": "Inter", "bb": "Banco do Brasil"}
GENERIC_MERCHANT_KEYS = {"pix", "compra", "debito", "credito", "pagamento", "transferencia",
                         "cartao", "estabelecimento", "lancamento"}


def merchant_description(description: str) -> str:
    """Remove bank wrappers and the fixed-width city/country suffix, keeping the merchant."""
    text = re.sub(r"^compra no d[eé]bito\s*[-:]\s*", "", description.strip(), flags=re.IGNORECASE)
    fields = re.split(r"\s{2,}", text)
    if len(fields) > 1 and re.search(r"(?:\s|^)BRA$", fields[-1], flags=re.IGNORECASE):
        merchant_fields = fields[:-2] if fields[-1].casefold() == "bra" else fields[:-1]
        if merchant_fields:
            text = " ".join(merchant_fields)
    return text


def merchant_key(description: str) -> str | None:
    """Match the same merchant across accents, casing and changing reference numbers."""
    plain = unicodedata.normalize("NFKD", merchant_description(description)).encode("ascii", "ignore").decode().casefold()
    plain = re.sub(r"\b(?:19|20)\d{2}\b", " ", plain)
    plain = re.sub(r"\b\d{5,}\b", " ", plain)
    key = " ".join(re.findall(r"[a-z0-9]+", plain))
    if len(key) < 4 or key in GENERIC_MERCHANT_KEYS or not re.search(r"[a-z]{4,}", key):
        return None
    return key


def parse_amount(value: str) -> int:
    """Convert a Brazilian or decimal amount to positive integer cents."""
    if not isinstance(value, str):
        raise ValidationError("Informe um valor válido.")
    raw = re.sub(r"\s+", "", value.replace("R$", ""))
    if not raw:
        raise ValidationError("Informe um valor válido.")
    if "," in raw:
        if not re.fullmatch(r"(?:\d{1,3}(?:\.\d{3})+|\d+),\d{1,2}", raw):
            raise ValidationError("Informe um valor válido.")
        whole, fraction = raw.split(",")
        raw = whole.replace(".", "") + "." + fraction
    elif re.fullmatch(r"\d{1,3}(?:\.\d{3})+", raw):
        raw = raw.replace(".", "")
    elif not re.fullmatch(r"\d+(?:\.\d{1,2})?", raw):
        raise ValidationError("Informe um valor válido com até duas casas decimais.")
    try:
        amount = Decimal(raw)
    except InvalidOperation as exc:
        raise ValidationError("Informe um valor válido.") from exc
    cents = int((amount * 100).quantize(Decimal("1"), rounding=ROUND_HALF_UP))
    if cents <= 0:
        raise ValidationError("O valor deve ser maior que zero.")
    if cents > 999_999_999_99:
        raise ValidationError("O valor é muito alto.")
    return cents


def validate_month(value: str) -> str:
    if not isinstance(value, str) or len(value) != 7:
        raise ValidationError("Use o mês no formato AAAA-MM.")
    try:
        date.fromisoformat(value + "-01")
    except ValueError as exc:
        raise ValidationError("Use um mês válido no formato AAAA-MM.") from exc
    return value


def validate_scope(value: str, allow_all: bool = False) -> str:
    if value not in (("personal", "business", "all") if allow_all else ("personal", "business")):
        raise ValidationError("Escolha Pessoal ou PJ.")
    return value


def validate_transaction(data: dict) -> dict:
    if not isinstance(data, dict):
        raise ValidationError("Dados do lançamento inválidos.")
    kind = data.get("kind")
    if kind not in ("income", "expense"):
        raise ValidationError("Escolha receita ou despesa.")
    excluded = data.get("excluded", False)
    scope = validate_scope(data.get("scope", "personal"))
    if not isinstance(excluded, bool):
        raise ValidationError("A opção de ignorar no resumo deve ser verdadeira ou falsa.")
    if not isinstance(data.get("description"), str) or not isinstance(data.get("category"), str):
        raise ValidationError("Informe descrição e categoria em texto.")
    description = data["description"].strip()
    category = data["category"].strip()
    if not description or len(description) > 120:
        raise ValidationError("A descrição deve ter de 1 a 120 caracteres.")
    if not category or len(category) > 60:
        raise ValidationError("A categoria deve ter de 1 a 60 caracteres.")
    occurred_on = data.get("occurred_on")
    try:
        parsed_date = date.fromisoformat(occurred_on)
        if parsed_date.isoformat() != occurred_on:
            raise ValueError
    except (TypeError, ValueError) as exc:
        raise ValidationError("Informe uma data válida no formato AAAA-MM-DD.") from exc
    return {
        "kind": kind,
        "description": description,
        "category": category,
        "amount_cents": parse_amount(data.get("amount")),
        "occurred_on": occurred_on,
        "excluded": int(excluded),
        "scope": scope,
    }


class FinanceStore:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._backup_before_migration()
        with self._connect() as connection:
            connection.execute("""
                CREATE TABLE IF NOT EXISTS transactions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    kind TEXT NOT NULL CHECK (kind IN ('income', 'expense')),
                    description TEXT NOT NULL,
                    category TEXT NOT NULL,
                    amount_cents INTEGER NOT NULL CHECK (amount_cents > 0),
                    occurred_on TEXT NOT NULL,
                    excluded INTEGER NOT NULL DEFAULT 0 CHECK (excluded IN (0, 1)),
                    source TEXT NOT NULL DEFAULT 'Manual',
                    import_key TEXT,
                    invoice_month TEXT,
                    scope TEXT NOT NULL DEFAULT 'personal' CHECK (scope IN ('personal', 'business')),
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                )
            """)
            existing_columns = {row["name"] for row in connection.execute("PRAGMA table_info(transactions)")}
            if "excluded" not in existing_columns:
                connection.execute("ALTER TABLE transactions ADD COLUMN excluded INTEGER NOT NULL DEFAULT 0")
            if "source" not in existing_columns:
                connection.execute("ALTER TABLE transactions ADD COLUMN source TEXT NOT NULL DEFAULT 'Manual'")
            if "import_key" not in existing_columns:
                connection.execute("ALTER TABLE transactions ADD COLUMN import_key TEXT")
            if "invoice_month" not in existing_columns:
                connection.execute("ALTER TABLE transactions ADD COLUMN invoice_month TEXT")
            if "scope" not in existing_columns:
                connection.execute("ALTER TABLE transactions ADD COLUMN scope TEXT NOT NULL DEFAULT 'personal'")
            if "pending" not in existing_columns:
                connection.execute("ALTER TABLE transactions ADD COLUMN pending INTEGER NOT NULL DEFAULT 0")
            connection.execute("CREATE INDEX IF NOT EXISTS transactions_date_idx ON transactions(occurred_on)")
            connection.execute("CREATE INDEX IF NOT EXISTS transactions_scope_date_idx ON transactions(scope, occurred_on)")
            connection.execute("CREATE UNIQUE INDEX IF NOT EXISTS transactions_import_key_idx ON transactions(import_key)")
            connection.execute("""
                CREATE TABLE IF NOT EXISTS account_balances (
                    source TEXT NOT NULL,
                    scope TEXT NOT NULL CHECK (scope IN ('personal', 'business')),
                    as_of TEXT NOT NULL,
                    amount_cents INTEGER NOT NULL,
                    PRIMARY KEY (source, scope, as_of)
                )
            """)
            connection.execute("""
                CREATE TABLE IF NOT EXISTS statement_coverage (
                    source TEXT NOT NULL,
                    scope TEXT NOT NULL CHECK (scope IN ('personal', 'business')),
                    starts_on TEXT NOT NULL,
                    ends_on TEXT NOT NULL,
                    PRIMARY KEY (source, scope, starts_on, ends_on)
                )
            """)
            connection.execute("""CREATE TABLE IF NOT EXISTS authoritative_periods (
                source TEXT NOT NULL, scope TEXT NOT NULL, starts_on TEXT NOT NULL, ends_on TEXT NOT NULL,
                checked_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY(source,scope,starts_on,ends_on))""")
            invoice_coverage_exists = connection.execute(
                "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'invoice_coverage'"
            ).fetchone() is not None
            connection.execute("""
                CREATE TABLE IF NOT EXISTS invoice_coverage (
                    source TEXT NOT NULL,
                    scope TEXT NOT NULL CHECK (scope IN ('personal', 'business')),
                    invoice_month TEXT NOT NULL,
                    PRIMARY KEY (source, scope, invoice_month)
                )
            """)
            if not invoice_coverage_exists:
                connection.execute("""
                    INSERT OR IGNORE INTO invoice_coverage (source, scope, invoice_month)
                    SELECT DISTINCT source, scope, invoice_month FROM transactions
                    WHERE invoice_month IS NOT NULL AND import_key IS NOT NULL
                """)
            connection.execute("""
                CREATE TABLE IF NOT EXISTS investment_entries (
                    import_key TEXT PRIMARY KEY,
                    occurred_at TEXT NOT NULL,
                    description TEXT NOT NULL,
                    amount_cents INTEGER NOT NULL,
                    balance_cents INTEGER NOT NULL,
                    matched_transaction_id INTEGER,
                    source TEXT NOT NULL DEFAULT 'XP'
                )
            """)
            connection.execute("""
                CREATE TABLE IF NOT EXISTS investment_snapshots (
                    as_of TEXT PRIMARY KEY,
                    total_cents INTEGER NOT NULL,
                    assets_cents INTEGER NOT NULL,
                    receivables_cents INTEGER NOT NULL,
                    cash_cents INTEGER NOT NULL
                )
            """)
            connection.execute("""
                CREATE TABLE IF NOT EXISTS investment_manual_snapshots (
                    as_of TEXT PRIMARY KEY,
                    total_cents INTEGER NOT NULL CHECK (total_cents > 0)
                )
            """)
            connection.execute("""
                CREATE TABLE IF NOT EXISTS investment_manual_flows (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    occurred_on TEXT NOT NULL,
                    kind TEXT NOT NULL CHECK (kind IN ('deposit', 'withdrawal')),
                    amount_cents INTEGER NOT NULL CHECK (amount_cents > 0)
                )
            """)
            connection.execute("""
                CREATE TABLE IF NOT EXISTS investment_positions (
                    as_of TEXT NOT NULL REFERENCES investment_snapshots(as_of),
                    name TEXT NOT NULL,
                    category TEXT NOT NULL,
                    value_cents INTEGER NOT NULL,
                    cost_cents INTEGER NOT NULL,
                    quantity TEXT,
                    net_cents INTEGER,
                    PRIMARY KEY (as_of, category, name)
                )
            """)
            connection.execute("""
                CREATE TABLE IF NOT EXISTS investment_assets (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name TEXT NOT NULL,
                    category TEXT NOT NULL,
                    UNIQUE(category, name)
                )
            """)
            position_columns = {row["name"] for row in connection.execute("PRAGMA table_info(investment_positions)")}
            for column, definition in (("cost_known", "INTEGER NOT NULL DEFAULT 1"), ("quote_date", "TEXT")):
                if column not in position_columns:
                    connection.execute(f"ALTER TABLE investment_positions ADD COLUMN {column} {definition}")
            snapshot_columns = {row["name"] for row in connection.execute("PRAGMA table_info(investment_snapshots)")}
            for column, definition in (("provider", "TEXT NOT NULL DEFAULT 'file'"),
                                       ("complete", "INTEGER NOT NULL DEFAULT 1"), ("checked_at", "TEXT")):
                if column not in snapshot_columns:
                    connection.execute(f"ALTER TABLE investment_snapshots ADD COLUMN {column} {definition}")
            connection.execute("""INSERT OR IGNORE INTO investment_assets (name, category)
                SELECT DISTINCT name, category FROM investment_positions""")
            flow_columns = {row["name"] for row in connection.execute(
                "PRAGMA table_info(investment_manual_flows)")}
            if "asset_id" not in flow_columns:
                connection.execute("ALTER TABLE investment_manual_flows ADD COLUMN asset_id INTEGER REFERENCES investment_assets(id)")
            if "external" not in flow_columns:
                connection.execute("""ALTER TABLE investment_manual_flows ADD COLUMN external INTEGER NOT NULL
                    DEFAULT 1 CHECK (external IN (0, 1))""")
            connection.execute("""
                CREATE TABLE IF NOT EXISTS market_quotes (
                    source TEXT NOT NULL,
                    identifier TEXT NOT NULL,
                    quote_date TEXT NOT NULL,
                    price TEXT NOT NULL,
                    checked_at TEXT NOT NULL,
                    PRIMARY KEY (source, identifier, quote_date)
                )
            """)
            connection.execute("""
                CREATE TABLE IF NOT EXISTS weekly_reports (
                    signature TEXT PRIMARY KEY,
                    generated_at TEXT NOT NULL,
                    report_json TEXT NOT NULL
                )
            """)
            connection.execute("""
                CREATE TABLE IF NOT EXISTS category_budgets (
                    scope TEXT NOT NULL CHECK (scope IN ('personal', 'business')),
                    category TEXT NOT NULL,
                    limit_cents INTEGER NOT NULL CHECK (limit_cents > 0),
                    PRIMARY KEY (scope, category)
                )
            """)
            connection.execute("""
                CREATE TABLE IF NOT EXISTS planned_cashflows (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    scope TEXT NOT NULL CHECK (scope IN ('personal', 'business')),
                    kind TEXT NOT NULL CHECK (kind IN ('income', 'expense')),
                    description TEXT NOT NULL,
                    amount_cents INTEGER NOT NULL CHECK (amount_cents > 0),
                    due_day INTEGER NOT NULL CHECK (due_day BETWEEN 1 AND 31),
                    starts_month TEXT NOT NULL
                )
            """)
            connection.execute("""
                CREATE TABLE IF NOT EXISTS investment_flow_classifications (
                    import_key TEXT PRIMARY KEY REFERENCES investment_entries(import_key),
                    classification TEXT NOT NULL CHECK (classification IN ('external', 'internal'))
                )
            """)
            flow_columns = {row["name"] for row in connection.execute("PRAGMA table_info(investment_flow_classifications)")}
            if "origin" not in flow_columns:
                connection.execute("ALTER TABLE investment_flow_classifications ADD COLUMN origin TEXT NOT NULL DEFAULT 'manual'")
            connection.execute("""
                CREATE TABLE IF NOT EXISTS card_due_rules (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    source TEXT NOT NULL,
                    scope TEXT NOT NULL CHECK (scope IN ('personal', 'business')),
                    due_day INTEGER NOT NULL CHECK (due_day BETWEEN 1 AND 31),
                    month_offset INTEGER NOT NULL CHECK (month_offset IN (0, 1)),
                    UNIQUE (source, scope)
                )
            """)
            connection.execute("""
                CREATE TABLE IF NOT EXISTS investment_news (
                    signature TEXT PRIMARY KEY,
                    as_of TEXT NOT NULL,
                    generated_at TEXT NOT NULL,
                    model TEXT NOT NULL,
                    report_json TEXT NOT NULL
                )
            """)
            memory_exists = connection.execute(
                "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'category_memory'"
            ).fetchone()
            connection.execute("""
                CREATE TABLE IF NOT EXISTS category_memory (
                    scope TEXT NOT NULL CHECK (scope IN ('personal', 'business')),
                    merchant_key TEXT NOT NULL,
                    category TEXT NOT NULL,
                    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    PRIMARY KEY (scope, merchant_key)
                )
            """)
            if not memory_exists:
                for row in connection.execute(
                    "SELECT description, scope, category FROM transactions "
                    "WHERE kind = 'expense' AND excluded = 0 ORDER BY id"
                ):
                    self._remember_category(connection, row["description"], row["scope"], row["category"])
            # Add aliases from approved historical descriptions without replacing a later correction.
            aliases = {}
            for row in connection.execute("SELECT description,scope,category FROM transactions WHERE kind='expense' AND excluded=0"):
                key = merchant_key(row["description"])
                if key and row["category"] in PURCHASE_CATEGORIES:
                    aliases.setdefault((row["scope"], key), set()).add(row["category"])
            for (scope, key), categories in aliases.items():
                if len(categories) == 1:
                    connection.execute("INSERT OR IGNORE INTO category_memory(scope,merchant_key,category) VALUES (?,?,?)",
                                       (scope, key, categories.pop()))
        os.chmod(self.path, 0o600)

    @staticmethod
    def _remember_category(connection: sqlite3.Connection, description: str,
                           scope: str, category: str) -> None:
        key = merchant_key(description)
        if key and category in PURCHASE_CATEGORIES:
            connection.execute("""
                INSERT INTO category_memory (scope, merchant_key, category) VALUES (?, ?, ?)
                ON CONFLICT (scope, merchant_key) DO UPDATE SET
                    category = excluded.category, updated_at = CURRENT_TIMESTAMP
            """, (scope, key, category))

    def remembered_categories(self, rows: list[dict]) -> dict[int, str]:
        """Return locally approved categories for the exact normalized merchant."""
        keys = {merchant_key(row["description"]) for row in rows}
        keys.discard(None)
        if not keys:
            return {}
        placeholders = ", ".join("?" for _ in keys)
        with self._connect() as connection:
            remembered = {
                (row["scope"], row["merchant_key"]): row["category"]
                for row in connection.execute(
                    f"SELECT scope, merchant_key, category FROM category_memory "
                    f"WHERE merchant_key IN ({placeholders})", tuple(keys))
            }
        matches = {}
        for row in rows:
            key = merchant_key(row["description"])
            category = remembered.get((row["scope"], key))
            if category:
                matches[row["id"]] = category
        return matches

    def apply_remembered_categories(self) -> int:
        """Apply only locally approved merchant rules to unclassified purchases."""
        with self._connect() as connection:
            rows = [dict(row) for row in connection.execute(
                "SELECT id,description,scope FROM transactions WHERE kind='expense' AND excluded=0 AND category='Importado'")]
        learned = self.remembered_categories(rows)
        applied = 0
        with self._connect() as connection:
            for identifier, category in learned.items():
                applied += connection.execute(
                    "UPDATE transactions SET category=? WHERE id=? AND category='Importado' AND excluded=0",
                    (category, identifier)).rowcount
        return applied

    def _backup_before_migration(self) -> None:
        if not self.path.exists():
            return
        with closing(sqlite3.connect(self.path)) as source:
            columns = {row[1] for row in source.execute("PRAGMA table_info(transactions)")}
            previous_columns = {"excluded", "source", "import_key", "invoice_month"}
            if not columns:
                return
            if previous_columns | {"scope"} <= columns:
                if source.execute("SELECT 1 FROM sqlite_master WHERE type = 'table' "
                                  "AND name = 'category_memory'").fetchone():
                    return
                suffix = ".before-category-memory.bak"
            else:
                suffix = ".before-scope.bak" if previous_columns <= columns else ".before-import.bak"
            backup_path = self.path.with_name(self.path.name + suffix)
            if backup_path.exists():
                os.chmod(backup_path, 0o600)
                return
            with closing(sqlite3.connect(backup_path)) as backup:
                source.backup(backup)
            os.chmod(backup_path, 0o600)

    @contextmanager
    def _connect(self):
        connection = sqlite3.connect(self.path)
        connection.row_factory = sqlite3.Row
        try:
            with connection:
                yield connection
        finally:
            connection.close()

    def add(self, data: dict) -> dict:
        item = validate_transaction(data)
        with self._connect() as connection:
            cursor = connection.execute(
                "INSERT INTO transactions (kind, description, category, amount_cents, occurred_on, excluded, scope) "
                "VALUES (:kind, :description, :category, :amount_cents, :occurred_on, :excluded, :scope)", item,
            )
            transaction_id = cursor.lastrowid
            if item["kind"] == "expense" and not item["excluded"]:
                self._remember_category(connection, item["description"], item["scope"], item["category"])
        return self.get(transaction_id)

    def add_card_purchase(self, data: dict) -> dict:
        if not isinstance(data, dict):
            raise ValidationError("Dados da compra inválidos.")
        bank = data.get("bank")
        account = data.get("account")
        if not isinstance(bank, str) or bank not in CARD_BANKS:
            raise ValidationError("Escolha Inter ou Banco do Brasil.")
        if not isinstance(account, str) or not 1 <= len(account.strip()) <= 50:
            raise ValidationError("Identifique o cartão com até 50 caracteres.")
        invoice_month = validate_month(data.get("invoice_month"))
        item = validate_transaction({**data, "kind": "expense", "excluded": False})
        if invoice_month < item["occurred_on"][:7]:
            raise ValidationError("O mês da fatura não pode ser anterior à compra.")
        if item["category"] == "Pagamento de fatura":
            raise ValidationError("Escolha uma categoria de compra para o cartão.")
        source = f"{CARD_BANKS[bank]} · {account.strip()}"
        with self._connect() as connection:
            cursor = connection.execute(
                "INSERT INTO transactions (kind, description, category, amount_cents, occurred_on, "
                "excluded, source, invoice_month, scope) VALUES (:kind, :description, :category, :amount_cents, "
                ":occurred_on, :excluded, :source, :invoice_month, :scope)",
                {**item, "source": source, "invoice_month": invoice_month},
            )
            transaction_id = cursor.lastrowid
            self._remember_category(connection, item["description"], item["scope"], item["category"])
        return self.get(transaction_id)

    def get(self, transaction_id: int) -> dict:
        with self._connect() as connection:
            row = connection.execute("SELECT * FROM transactions WHERE id = ?", (transaction_id,)).fetchone()
        if row is None:
            raise NotFoundError("Lançamento não encontrado.")
        return dict(row)

    def update(self, transaction_id: int, data: dict) -> dict:
        item = validate_transaction({**data, "scope": data.get("scope", self.get(transaction_id)["scope"])})
        with self._connect() as connection:
            cursor = connection.execute(
                "UPDATE transactions SET kind = :kind, description = :description, "
                "category = :category, amount_cents = :amount_cents, occurred_on = :occurred_on, "
                "excluded = :excluded, scope = :scope "
                "WHERE id = :id", {**item, "id": transaction_id},
            )
            if cursor.rowcount == 0:
                raise NotFoundError("Lançamento não encontrado.")
            if item["kind"] == "expense" and not item["excluded"]:
                self._remember_category(connection, item["description"], item["scope"], item["category"])
        return self.get(transaction_id)

    def delete(self, transaction_id: int) -> None:
        with self._connect() as connection:
            cursor = connection.execute("DELETE FROM transactions WHERE id = ?", (transaction_id,))
            if cursor.rowcount == 0:
                raise NotFoundError("Lançamento não encontrado.")

    def list_month(self, month: str, scope: str = "personal") -> list[dict]:
        month = validate_month(month)
        scope = validate_scope(scope, allow_all=True)
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT * FROM transactions WHERE occurred_on >= ? AND occurred_on < ? "
                "AND (? = 'all' OR scope = ?) ORDER BY occurred_on DESC, id DESC",
                (month + "-01", _next_month(month) + "-01", scope, scope),
            ).fetchall()
        return [dict(row) for row in rows]

    def add_imported(self, items: list[dict]) -> dict:
        if not items or len(items) > 5000:
            raise ValidationError("O arquivo deve conter de 1 a 5000 lançamentos.")
        prepared = []
        for item in items:
            amount_cents = item.get("amount_cents")
            if not isinstance(amount_cents, int) or amount_cents <= 0:
                raise ValidationError("Valor inválido no arquivo.")
            validated = validate_transaction({
                **item,
                "amount": str(Decimal(amount_cents) / 100),
                "excluded": item.get("excluded", False),
            })
            source = item.get("source")
            import_key = item.get("import_key")
            invoice_month = item.get("invoice_month")
            scope = validate_scope(item.get("scope", "personal"))
            if not isinstance(source, str) or not source or len(source) > 80:
                raise ValidationError("Origem do arquivo inválida.")
            if not isinstance(import_key, str) or not re.fullmatch(r"[0-9a-f]{64}", import_key):
                raise ValidationError("Identificador de importação inválido.")
            if invoice_month is not None:
                invoice_month = validate_month(invoice_month)
            prepared.append({**validated, "source": source, "import_key": import_key,
                             "invoice_month": invoice_month, "scope": scope})
        imported = 0
        with self._connect() as connection:
            for item in prepared:
                cursor = connection.execute(
                    "INSERT INTO transactions (kind, description, category, amount_cents, occurred_on, "
                    "excluded, source, import_key, invoice_month, scope) VALUES (:kind, :description, :category, :amount_cents, "
                    ":occurred_on, :excluded, :source, :import_key, :invoice_month, :scope) "
                    "ON CONFLICT(import_key) DO NOTHING", item,
                )
                imported += cursor.rowcount
                if cursor.rowcount == 0 and item["excluded"] and item["category"] != "Importado":
                    connection.execute(
                        "UPDATE transactions SET excluded = 1, category = ? "
                        "WHERE import_key = ? AND category = 'Importado' AND excluded = 0",
                        (item["category"], item["import_key"]),
                    )
        return {"imported": imported, "skipped": len(prepared) - imported}

    def save_account_balance(self, source: str, scope: str, as_of: str, amount_cents: int) -> None:
        scope = validate_scope(scope)
        if not isinstance(source, str) or not 1 <= len(source) <= 80:
            raise ValidationError("Origem do saldo inválida.")
        try:
            if date.fromisoformat(as_of).isoformat() != as_of:
                raise ValueError
        except (TypeError, ValueError) as exc:
            raise ValidationError("Data do saldo inválida.") from exc
        if not isinstance(amount_cents, int) or isinstance(amount_cents, bool):
            raise ValidationError("Saldo inválido.")
        with self._connect() as connection:
            connection.execute(
                "INSERT INTO account_balances (source, scope, as_of, amount_cents) VALUES (?, ?, ?, ?) "
                "ON CONFLICT(source, scope, as_of) DO UPDATE SET amount_cents = excluded.amount_cents",
                (source, scope, as_of, amount_cents),
            )

    def save_invoice_coverage(self, source: str, scope: str, invoice_month: str) -> None:
        scope = validate_scope(scope)
        invoice_month = validate_month(invoice_month)
        if not isinstance(source, str) or not 1 <= len(source) <= 80:
            raise ValidationError("Origem da fatura inválida.")
        with self._connect() as connection:
            connection.execute(
                "INSERT OR IGNORE INTO invoice_coverage (source, scope, invoice_month) VALUES (?, ?, ?)",
                (source, scope, invoice_month),
            )

    def has_uncovered_invoice_imports(self, source: str, scope: str, invoice_month: str) -> bool:
        with self._connect() as connection:
            return connection.execute("""
                SELECT 1 FROM transactions AS t
                WHERE t.source = ? AND t.scope = ? AND t.invoice_month = ?
                  AND t.import_key IS NOT NULL
                  AND NOT EXISTS (
                      SELECT 1 FROM invoice_coverage AS c
                      WHERE c.source = t.source AND c.scope = t.scope
                        AND c.invoice_month = t.invoice_month
                  )
                LIMIT 1
            """, (source, scope, invoice_month)).fetchone() is not None

    def save_account_statement(self, source: str, scope: str, period: dict | None,
                               daily_balances: list[dict], latest_balance: dict | None = None) -> None:
        """Save statement coverage and dated balances after a successful import."""
        scope = validate_scope(scope)
        if not isinstance(source, str) or not 1 <= len(source) <= 80:
            raise ValidationError("Origem do saldo inválida.")
        balances = {row["as_of"]: row["amount_cents"] for row in daily_balances}
        if latest_balance:
            balances[latest_balance["as_of"]] = latest_balance["amount_cents"]
        for as_of, amount_cents in balances.items():
            try:
                if date.fromisoformat(as_of).isoformat() != as_of:
                    raise ValueError
            except (TypeError, ValueError) as exc:
                raise ValidationError("Data do saldo inválida.") from exc
            if not isinstance(amount_cents, int) or isinstance(amount_cents, bool):
                raise ValidationError("Saldo inválido.")
        if period:
            starts_on, ends_on = period["starts_on"], period["ends_on"]
            try:
                if date.fromisoformat(starts_on).isoformat() != starts_on or date.fromisoformat(ends_on).isoformat() != ends_on or starts_on > ends_on:
                    raise ValueError
            except (TypeError, ValueError) as exc:
                raise ValidationError("Período do extrato inválido.") from exc
        with self._connect() as connection:
            connection.executemany(
                "INSERT INTO account_balances (source, scope, as_of, amount_cents) VALUES (?, ?, ?, ?) "
                "ON CONFLICT(source, scope, as_of) DO UPDATE SET amount_cents = excluded.amount_cents",
                [(source, scope, as_of, amount_cents) for as_of, amount_cents in balances.items()],
            )
            if period:
                connection.execute(
                    "INSERT OR IGNORE INTO statement_coverage (source, scope, starts_on, ends_on) VALUES (?, ?, ?, ?)",
                    (source, scope, starts_on, ends_on),
                )

    def analytics(self, end_month: str, scope: str = "personal", months: int = 6) -> dict:
        """Return chart data with explicit cash statement coverage."""
        end_month = validate_month(end_month)
        scope = validate_scope(scope, allow_all=True)
        if isinstance(months, bool) or not isinstance(months, int) or not 1 <= months <= 24:
            raise ValidationError("Escolha de 1 a 24 meses para os gráficos.")
        month_list = [end_month]
        for _ in range(months - 1):
            month_list.insert(0, _previous_month(month_list[0]))
        first_day = month_list[0] + "-01"
        last_day = _next_month(end_month) + "-01"
        with self._connect() as connection:
            coverage_rows = [dict(row) for row in connection.execute(
                "SELECT source, scope, starts_on, ends_on FROM statement_coverage "
                "WHERE ends_on >= ? AND starts_on < ? AND (? = 'all' OR scope = ?)",
                (first_day, last_day, scope, scope),
            )]
            known_sources = [tuple(row) for row in connection.execute(
                "SELECT DISTINCT source, scope FROM statement_coverage WHERE (? = 'all' OR scope = ?)",
                (scope, scope),
            )]
            balance_rows = [dict(row) for row in connection.execute(
                "SELECT source, scope, as_of, amount_cents FROM account_balances "
                "WHERE as_of >= ? AND as_of < ? AND (? = 'all' OR scope = ?) "
                "ORDER BY scope, source, as_of",
                (first_day, last_day, scope, scope),
            )]
            category_rows = [dict(row) for row in connection.execute("""
                SELECT substr(occurred_on, 1, 7) AS month, category,
                       SUM(amount_cents) AS amount_cents
                FROM transactions
                WHERE kind = 'expense' AND excluded = 0 AND category != 'Pagamento de fatura'
                  AND occurred_on >= ? AND occurred_on < ?
                  AND (? = 'all' OR scope = ?)
                GROUP BY month, category ORDER BY month, amount_cents DESC, category
            """, (first_day, last_day, scope, scope))]
        invoices_by_month: dict[str, dict] = {}
        for invoice in self.invoice_history(scope):
            month = invoice["invoice_month"]
            entry = invoices_by_month.setdefault(month, {"total_cents": 0, "statement_count": 0})
            entry["total_cents"] += invoice["expense_cents"] - invoice["credit_cents"]
            entry["statement_count"] += 1
        cashflow = []
        invoices = []
        for month in month_list:
            start, end = month + "-01", _next_month(month) + "-01"
            overlapping = [row for row in coverage_rows if row["starts_on"] < end and row["ends_on"] >= start]
            account_items = [item for item in self.list_month(month, scope) if item["invoice_month"] is None]
            if not overlapping and not account_items:
                coverage = "none"
            elif known_sources and all(_month_fully_covered(
                    [row for row in overlapping if (row["source"], row["scope"]) == source],
                    start, date.fromordinal(date.fromisoformat(end).toordinal() - 1).isoformat(),
            ) for source in known_sources):
                coverage = "full"
            else:
                coverage = "partial"
            summary = self.summary(month, scope) if coverage != "none" else None
            cashflow.append({"month": month, "coverage": coverage,
                             "income_cents": summary["income_cents"] if summary else None,
                             "expense_cents": summary["expense_cents"] if summary else None})
            invoice = invoices_by_month.get(month)
            invoices.append({"month": month,
                             "total_cents": invoice["total_cents"] if invoice else None,
                             "statement_count": invoice["statement_count"] if invoice else 0})
        series: dict[tuple[str, str], list[dict]] = {}
        for row in balance_rows:
            series.setdefault((row["source"], row["scope"]), []).append(
                {"date": row["as_of"], "amount_cents": row["amount_cents"]})
        spending_by_month: dict[str, list[dict]] = {month: [] for month in month_list}
        for row in category_rows:
            spending_by_month[row["month"]].append(
                {"category": row["category"], "amount_cents": row["amount_cents"]})
        return {"scope": scope, "end_month": end_month, "months": month_list,
                "cashflow": cashflow, "invoices": invoices,
                "category_spending": [
                    {"month": month, "total_cents": sum(item["amount_cents"] for item in spending_by_month[month]),
                     "categories": spending_by_month[month]}
                    for month in month_list],
                "balance_series": [{"source": source, "scope": account_scope, "points": points}
                                   for (source, account_scope), points in series.items()]}

    def account_balance_snapshots(self, month: str, scope: str = "personal") -> list[dict]:
        month = validate_month(month)
        scope = validate_scope(scope, allow_all=True)
        with self._connect() as connection:
            rows = connection.execute("""
                SELECT balances.source, balances.scope, balances.as_of, balances.amount_cents
                FROM account_balances AS balances
                WHERE balances.as_of < ? AND (? = 'all' OR balances.scope = ?)
                  AND balances.as_of = (
                      SELECT MAX(candidate.as_of) FROM account_balances AS candidate
                      WHERE candidate.source = balances.source AND candidate.scope = balances.scope
                        AND candidate.as_of < ?
                  )
                ORDER BY balances.scope, balances.source
            """, (_next_month(month) + "-01", scope, scope, _next_month(month) + "-01")).fetchall()
        return [dict(row) for row in rows]

    def reconcile_internal_transfers(self) -> int:
        """Exclude unique same-day PJ→PF transfers with matching counterparty names."""
        with self._connect() as connection:
            rows = connection.execute("""
                SELECT id, scope, kind, occurred_on, amount_cents, description
                FROM transactions
                WHERE invoice_month IS NULL AND excluded = 0 AND category = 'Importado'
                  AND ((scope = 'business' AND kind = 'expense')
                    OR (scope = 'personal' AND kind = 'income'))
            """).fetchall()
            outgoing: dict[tuple[str, int], list] = {}
            incoming: dict[tuple[str, int], list] = {}
            for row in rows:
                target = outgoing if row["scope"] == "business" else incoming
                target.setdefault((row["occurred_on"], row["amount_cents"]), []).append(row)
            matched = 0
            for key, business_rows in outgoing.items():
                personal_rows = incoming.get(key, [])
                if len(business_rows) != 1 or len(personal_rows) != 1:
                    continue
                left = _description_words(business_rows[0]["description"])
                right = _description_words(personal_rows[0]["description"])
                if len(left & right) < 2 or len(left & right) / len(left | right) < 0.8:
                    continue
                connection.execute(
                    "UPDATE transactions SET excluded = 1, category = 'Transferência entre contas' "
                    "WHERE id IN (?, ?)", (business_rows[0]["id"], personal_rows[0]["id"]),
                )
                matched += 1
        return matched

    @staticmethod
    def _xp_matches(connection: sqlite3.Connection, entries: list[dict]) -> list[dict]:
        """Require one same-day, same-value Inter debit with the same payee words."""
        from collections import defaultdict

        incoming = defaultdict(list)
        for entry in entries:
            if entry["amount_cents"] > 0 and ("pix recebido" in entry["description"].casefold() or entry.get("classification") == "external"):
                incoming[(entry["occurred_on"], entry["amount_cents"])].append(entry)
        if not incoming:
            return []
        outgoing = defaultdict(list)
        for row in connection.execute("""
            SELECT id, occurred_on, amount_cents, description, scope FROM transactions
            WHERE kind = 'expense' AND (category = 'Transferência para investimento' OR
                                       (excluded = 0 AND category = 'Importado'))
              AND invoice_month IS NULL AND source LIKE 'Inter · %'
        """):
            outgoing[(row["occurred_on"], row["amount_cents"])].append(row)
        matches = []
        for key, xp_rows in incoming.items():
            inter_rows = outgoing.get(key, [])
            if len(xp_rows) != 1 or len(inter_rows) != 1:
                continue
            xp, inter = xp_rows[0], inter_rows[0]
            left, right = _description_words(xp["description"]), _description_words(inter["description"])
            if len(left & right) < 2 or len(left & right) / len(left | right) < 0.8:
                continue
            matches.append({"xp_import_key": xp["import_key"], "transaction_id": inter["id"],
                            "occurred_on": key[0], "amount_cents": key[1],
                            "description": inter["description"], "scope": inter["scope"]})
        return sorted(matches, key=lambda item: (item["occurred_on"], item["transaction_id"]))

    def preview_xp_statement(self, entries: list[dict]) -> dict:
        with self._connect() as connection:
            matches = self._xp_matches(connection, entries)
            known = connection.execute(
                "SELECT COUNT(*) FROM investment_entries WHERE import_key IN (" +
                ",".join("?" for _ in entries) + ")", [entry["import_key"] for entry in entries]
            ).fetchone()[0]
        return {"count": len(entries), "already_imported": known, "matches": matches,
                "sample": entries[:5]}

    def import_xp_statement(self, entries: list[dict]) -> dict:
        imported = 0
        matched = 0
        with self._connect() as connection:
            matches = self._xp_matches(connection, entries)
            for entry in entries:
                cursor = connection.execute("""
                    INSERT INTO investment_entries
                        (import_key, occurred_at, description, amount_cents, balance_cents)
                    VALUES (:import_key, :occurred_at, :description, :amount_cents, :balance_cents)
                    ON CONFLICT(import_key) DO NOTHING
                """, entry)
                imported += cursor.rowcount
            for match in matches:
                cursor = connection.execute("""
                    UPDATE transactions SET category = 'Transferência para investimento', excluded = 1
                    WHERE id = ? AND category = 'Importado' AND excluded = 0
                """, (match["transaction_id"],))
                if cursor.rowcount:
                    connection.execute("""
                        UPDATE investment_entries SET matched_transaction_id = ? WHERE import_key = ?
                    """, (match["transaction_id"], match["xp_import_key"]))
                    matched += 1
        return {"imported": imported, "skipped": len(entries) - imported,
                "matched_transfers": matched}

    def preview_xp_positions(self, snapshot: dict) -> dict:
        with self._connect() as connection:
            existing = connection.execute(
                "SELECT 1 FROM investment_snapshots WHERE as_of = ?", (snapshot["as_of"],)
            ).fetchone()
        return {**snapshot, "replaces_existing": bool(existing)}

    def import_xp_positions(self, snapshot: dict) -> dict:
        with self._connect() as connection:
            replacing = bool(connection.execute(
                "SELECT 1 FROM investment_snapshots WHERE as_of = ?", (snapshot["as_of"],)
            ).fetchone())
            connection.execute("""
                INSERT INTO investment_snapshots
                    (as_of, total_cents, assets_cents, receivables_cents, cash_cents)
                VALUES (:as_of, :total_cents, :assets_cents, :receivables_cents, :cash_cents)
                ON CONFLICT(as_of) DO UPDATE SET
                    total_cents = excluded.total_cents,
                    assets_cents = excluded.assets_cents,
                    receivables_cents = excluded.receivables_cents,
                    cash_cents = excluded.cash_cents
            """, snapshot)
            connection.execute("DELETE FROM investment_positions WHERE as_of = ?", (snapshot["as_of"],))
            connection.executemany("""
                INSERT INTO investment_positions
                    (as_of, name, category, value_cents, cost_cents, quantity, net_cents)
                VALUES (:as_of, :name, :category, :value_cents, :cost_cents, :quantity, :net_cents)
            """, [{**asset, "as_of": snapshot["as_of"]} for asset in snapshot["assets"]])
            connection.executemany("""INSERT OR IGNORE INTO investment_assets (name, category)
                VALUES (:name, :category)""", snapshot["assets"])
        return {"as_of": snapshot["as_of"], "total_cents": snapshot["total_cents"],
                "asset_count": len(snapshot["assets"]), "replaced": replacing}

    def investment_overview(self, end_month: str, months: int = 6) -> dict:
        """Report deposits and dated XP portfolio snapshots as separate measures."""
        from manual_investments import imported_flow_kind, manual_overview

        end_month = validate_month(end_month)
        if isinstance(months, bool) or not isinstance(months, int) or not 1 <= months <= 24:
            raise ValidationError("Escolha de 1 a 24 meses para os investimentos.")
        month_list = [end_month]
        for _ in range(months - 1):
            month_list.insert(0, _previous_month(month_list[0]))
        first_day = month_list[0] + "-01"
        last_day = _next_month(end_month) + "-01"
        with self._connect() as connection:
            rows = [dict(row) for row in connection.execute("""
                SELECT e.occurred_at, e.description, e.amount_cents, e.balance_cents,
                       e.matched_transaction_id, c.classification AS flow_classification
                FROM investment_entries AS e LEFT JOIN investment_flow_classifications AS c
                  ON c.import_key = e.import_key
                WHERE e.occurred_at < ? ORDER BY e.occurred_at
            """, (last_day,))]
            snapshot_row = connection.execute("""
                SELECT * FROM investment_snapshots WHERE as_of < ? ORDER BY as_of DESC LIMIT 1
            """, (last_day,)).fetchone()
            position = dict(snapshot_row) if snapshot_row else None
            if position:
                position["assets"] = [dict(row) for row in connection.execute("""
                    SELECT name, category, value_cents, cost_cents, quantity, net_cents, cost_known, quote_date
                    FROM investment_positions WHERE as_of = ? ORDER BY value_cents DESC, name
                """, (position["as_of"],))]
            snapshots = [dict(row) for row in connection.execute("""
                SELECT as_of, total_cents FROM investment_snapshots
                WHERE as_of >= ? AND as_of < ? ORDER BY as_of
            """, (first_day, last_day))]
        manual = manual_overview(self)
        manual_positions = [row for row in manual["snapshots"] if row["as_of"] < last_day]
        latest_totals = {}
        if position:
            latest_totals[position["as_of"]] = {"as_of": position["as_of"],
                                                 "total_cents": position["total_cents"], "source": "xp"}
        latest_totals.update({row["as_of"]: {**row, "source": "manual"} for row in manual_positions})
        latest_total = max(latest_totals.values(), key=lambda row: row["as_of"]) if latest_totals else None
        for flow in manual["flows"]:
            if flow["occurred_on"] >= last_day or not flow["external"] or flow["matched_import"]:
                continue
            rows.append({"occurred_at": flow["occurred_on"] + "T12:00:00",
                         "description": "Aporte manual" if flow["kind"] == "deposit" else "Resgate manual",
                         "amount_cents": flow["amount_cents"] * (1 if flow["kind"] == "deposit" else -1),
                         "balance_cents": 0, "matched_transaction_id": None,
                         "manual_kind": flow["kind"]})
        rows.sort(key=lambda row: row["occurred_at"])
        deposited_by_month = {month: 0 for month in month_list}
        seen_months = set()
        total_deposited = 0
        period_deposited = 0
        matched_count = 0
        matched_cents = 0
        movements = []
        for row in rows:
            occurred_on = row["occurred_at"][:10]
            month = occurred_on[:7]
            is_deposit = row.get("manual_kind") == "deposit" or (
                row["amount_cents"] > 0 and
                (row.get("flow_classification") or imported_flow_kind(
                    row["description"], row["amount_cents"])) == "external")
            if is_deposit:
                total_deposited += row["amount_cents"]
            if occurred_on < first_day:
                continue
            seen_months.add(month)
            if is_deposit:
                deposited_by_month[month] += row["amount_cents"]
                period_deposited += row["amount_cents"]
                if row["matched_transaction_id"] is not None:
                    matched_count += 1
                    matched_cents += row["amount_cents"]
            movements.append({"occurred_at": row["occurred_at"],
                              "description": row["description"],
                              "amount_cents": row["amount_cents"],
                              "type": "withdrawal" if row.get("manual_kind") == "withdrawal" else
                                      "deposit" if is_deposit else
                                      "allocation" if row["amount_cents"] < 0 and
                                      ("investimento" in row["description"].casefold() or
                                       "apl." in row["description"].casefold()) else "other",
                              "matched_inter": bool(row["matched_transaction_id"]),
                              "manual": bool(row.get("manual_kind"))})
        cumulative = total_deposited - period_deposited
        series = []
        first_seen = next((month for month in month_list if month in seen_months), None)
        for month in month_list:
            if first_seen is not None and month >= first_seen:
                cumulative += deposited_by_month[month]
                accumulated = cumulative
            else:
                accumulated = None
            series.append({"month": month,
                           "deposits_cents": deposited_by_month[month] if month in seen_months else None,
                           "cumulative_cents": accumulated})
        return {"end_month": end_month, "months": series,
                "total_deposited_cents": total_deposited,
                "period_deposited_cents": period_deposited,
                "matched_count": matched_count, "matched_cents": matched_cents,
                "movement_count": len(movements),
                "movements": list(reversed(movements[-60:])),
                "position": position, "position_history": snapshots, "latest_total": latest_total}

    def latest_investment_position(self) -> dict | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM investment_snapshots ORDER BY as_of DESC LIMIT 1"
            ).fetchone()
            if row is None:
                return None
            position = dict(row)
            position["assets"] = [dict(asset) for asset in connection.execute("""
                SELECT name, category, value_cents, cost_cents, quantity, cost_known, quote_date
                FROM investment_positions WHERE as_of = ? ORDER BY value_cents DESC, name
            """, (position["as_of"],))]
            return position

    def save_market_quotes(self, quotes: list[dict]) -> None:
        with self._connect() as connection:
            connection.executemany("""
                INSERT INTO market_quotes (source, identifier, quote_date, price, checked_at)
                VALUES (:source, :identifier, :quote_date, :price, :checked_at)
                ON CONFLICT(source, identifier, quote_date) DO UPDATE SET
                    price = excluded.price, checked_at = excluded.checked_at
            """, quotes)

    def market_quotes(self, source: str, identifier: str) -> list[dict]:
        with self._connect() as connection:
            return [dict(row) for row in connection.execute("""
                SELECT quote_date, price, checked_at FROM market_quotes
                WHERE source = ? AND identifier = ? ORDER BY quote_date DESC
            """, (source, identifier))]

    def investment_news_context(self) -> dict | None:
        """Return asset names only; amounts and account details never enter research prompts."""
        with self._connect() as connection:
            row = connection.execute("SELECT as_of FROM investment_snapshots ORDER BY as_of DESC LIMIT 1").fetchone()
            if row is None:
                return None
            all_names = [item[0] for item in connection.execute("""
                SELECT name FROM investment_positions WHERE as_of = ?
                ORDER BY value_cents DESC, name
            """, (row["as_of"],))]
            names = all_names[:8]
        signature = hashlib.sha256((row["as_of"] + "|" + "|".join(names)).encode("utf-8")).hexdigest()
        return {"as_of": row["as_of"], "assets": names, "more_assets": max(0, len(all_names) - 8),
                "signature": signature}

    def investment_news(self) -> dict:
        context = self.investment_news_context()
        if context is None:
            return {"position": None, "report": None}
        with self._connect() as connection:
            row = connection.execute("""
                SELECT generated_at, model, report_json FROM investment_news WHERE signature = ?
            """, (context["signature"],)).fetchone()
        report = ({"generated_at": row["generated_at"], "model": row["model"],
                   **json.loads(row["report_json"])} if row else None)
        return {"position": {"as_of": context["as_of"], "assets": context["assets"],
                             "more_assets": context["more_assets"]},
                "report": report}

    def save_investment_news(self, context: dict, report: dict, generated_at: str, model: str) -> dict:
        with self._connect() as connection:
            connection.execute("""
                INSERT INTO investment_news (signature, as_of, generated_at, model, report_json)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(signature) DO UPDATE SET generated_at = excluded.generated_at,
                    model = excluded.model, report_json = excluded.report_json
            """, (context["signature"], context["as_of"], generated_at, model,
                  json.dumps(report, ensure_ascii=False)))
        return {"generated_at": generated_at, "model": model, **report}

    def invoice_history(self, scope: str = "personal") -> list[dict]:
        scope = validate_scope(scope, allow_all=True)
        with self._connect() as connection:
            rows = connection.execute("""
                SELECT invoice_month, source, scope,
                    COUNT(*) AS transaction_count,
                    SUM(pending) AS pending_count,
                    SUM(CASE WHEN kind = 'expense' THEN amount_cents ELSE 0 END) AS expense_cents,
                    SUM(CASE WHEN kind = 'income' THEN amount_cents ELSE 0 END) AS credit_cents
                FROM transactions
                WHERE invoice_month IS NOT NULL AND (? = 'all' OR scope = ?)
                GROUP BY invoice_month, source, scope
                ORDER BY invoice_month DESC, source
            """, (scope, scope)).fetchall()
        return [dict(row) for row in rows]

    def uncategorized_purchases(self, scope: str = "personal", month: str | None = None,
                                limit: int = 40, payment_type: str = "all") -> tuple[list[dict], int]:
        scope = validate_scope(scope, allow_all=True)
        if month is not None:
            month = validate_month(month)
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 40:
            raise ValidationError("Selecione até 40 compras por vez.")
        if payment_type not in ("all", "account", "credit"):
            raise ValidationError("Tipo de compra inválido.")
        start = month + "-01" if month else None
        end = _next_month(month) + "-01" if month else None
        conditions = ("kind = 'expense' AND excluded = 0 AND category = 'Importado' "
                      "AND (? = 'all' OR scope = ?) "
                      "AND (? IS NULL OR (occurred_on >= ? AND occurred_on < ?)) "
                      "AND (? = 'all' OR (? = 'account' AND invoice_month IS NULL) "
                      "OR (? = 'credit' AND invoice_month IS NOT NULL))")
        params = (scope, scope, month, start, end, payment_type, payment_type, payment_type)
        with self._connect() as connection:
            count = connection.execute(f"SELECT COUNT(*) FROM transactions WHERE {conditions}", params).fetchone()[0]
            rows = connection.execute(
                f"SELECT id, description, amount_cents, occurred_on, source, scope, invoice_month "
                f"FROM transactions WHERE {conditions} ORDER BY occurred_on DESC, id DESC LIMIT ?",
                (*params, limit),
            ).fetchall()
        return [dict(row) for row in rows], count

    def uncategorized_breakdown(self, month: str | None = None) -> dict:
        if month is not None:
            month = validate_month(month)
        start = month + "-01" if month else None
        end = _next_month(month) + "-01" if month else None
        result = {scope: {"account": 0, "credit": 0} for scope in ("personal", "business")}
        with self._connect() as connection:
            rows = connection.execute("""
                SELECT scope, CASE WHEN invoice_month IS NULL THEN 'account' ELSE 'credit' END AS payment_type,
                    COUNT(*) AS count
                FROM transactions
                WHERE kind = 'expense' AND excluded = 0 AND category = 'Importado'
                    AND (? IS NULL OR (occurred_on >= ? AND occurred_on < ?))
                GROUP BY scope, payment_type
            """, (month, start, end)).fetchall()
        for row in rows:
            result[row["scope"]][row["payment_type"]] = row["count"]
        return result

    def apply_purchase_categories(self, suggestions: list[dict], scope: str = "personal") -> dict:
        scope = validate_scope(scope, allow_all=True)
        if not isinstance(suggestions, list) or not 1 <= len(suggestions) <= 40:
            raise ValidationError("Envie de 1 a 40 categorias.")
        ids = set()
        for item in suggestions:
            if not isinstance(item, dict) or set(item) != {"id", "category"}:
                raise ValidationError("Sugestão de categoria inválida.")
            transaction_id, category = item["id"], item["category"]
            if isinstance(transaction_id, bool) or not isinstance(transaction_id, int) or transaction_id <= 0 or transaction_id in ids:
                raise ValidationError("ID de compra inválido ou repetido.")
            if category not in PURCHASE_CATEGORIES:
                raise ValidationError("Categoria de compra inválida.")
            ids.add(transaction_id)
        applied = 0
        with self._connect() as connection:
            for item in suggestions:
                original = connection.execute(
                    "SELECT description, scope FROM transactions WHERE id = ? AND kind = 'expense' "
                    "AND excluded = 0 AND category = 'Importado' AND (? = 'all' OR scope = ?)",
                    (item["id"], scope, scope),
                ).fetchone()
                if original is None:
                    continue
                cursor = connection.execute(
                    "UPDATE transactions SET category = ? WHERE id = ? AND kind = 'expense' "
                    "AND excluded = 0 AND category = 'Importado' AND (? = 'all' OR scope = ?)",
                    (item["category"], item["id"], scope, scope),
                )
                applied += cursor.rowcount
                if cursor.rowcount:
                    self._remember_category(connection, original["description"], original["scope"],
                                            item["category"])
        return {"applied": applied, "skipped": len(suggestions) - applied}

    def invoice_items(self, month: str, source: str, scope: str = "personal") -> list[dict]:
        month = validate_month(month)
        scope = validate_scope(scope)
        if not isinstance(source, str) or not 1 <= len(source) <= 80:
            raise ValidationError("Origem da fatura inválida.")
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT * FROM transactions WHERE invoice_month = ? AND source = ? AND scope = ? "
                "ORDER BY occurred_on, id", (month, source, scope),
            ).fetchall()
        return [dict(row) for row in rows]

    def purchase_categories(self, scope: str = "personal", month: str | None = None) -> dict:
        """Sum purchases for one month or the full history without bill payments."""
        scope = validate_scope(scope, allow_all=True)
        if month is not None:
            month = validate_month(month)
        start = month + "-01" if month else None
        end = _next_month(month) + "-01" if month else None
        with self._connect() as connection:
            rows = connection.execute("""
                SELECT category, SUM(amount_cents) AS amount_cents
                FROM transactions
                WHERE kind = 'expense' AND excluded = 0 AND category != 'Pagamento de fatura'
                  AND (? = 'all' OR scope = ?)
                  AND (? IS NULL OR (occurred_on >= ? AND occurred_on < ?))
                GROUP BY category ORDER BY amount_cents DESC, category
            """, (scope, scope, month, start, end)).fetchall()
        categories = [dict(row) for row in rows]
        return {"scope": scope, "month": month,
                "purchase_total_cents": sum(row["amount_cents"] for row in categories),
                "purchase_categories": categories}

    def summary(self, month: str, scope: str = "personal") -> dict:
        items = self.list_month(month, scope)
        account_items = [item for item in items if item["invoice_month"] is None]
        income = sum(item["amount_cents"] for item in account_items
                     if item["kind"] == "income" and not item["excluded"])
        expenses = sum(item["amount_cents"] for item in account_items
                       if item["kind"] == "expense" and (not item["excluded"] or item["category"] == "Pagamento de fatura"))
        card_payments = sum(item["amount_cents"] for item in account_items
                            if item["kind"] == "expense" and item["category"] == "Pagamento de fatura")
        card_purchases = sum(item["amount_cents"] for item in items
                             if item["invoice_month"] is not None and item["kind"] == "expense"
                             and not item["excluded"])
        by_category: dict[str, int] = {}
        for item in account_items:
            if item["kind"] == "expense" and (not item["excluded"] or item["category"] == "Pagamento de fatura"):
                by_category[item["category"]] = by_category.get(item["category"], 0) + item["amount_cents"]
        purchases_by_category: dict[str, int] = {}
        for item in items:
            if item["kind"] == "expense" and not item["excluded"] and item["category"] != "Pagamento de fatura":
                purchases_by_category[item["category"]] = (
                    purchases_by_category.get(item["category"], 0) + item["amount_cents"])
        purchase_total = sum(purchases_by_category.values())
        snapshots = self.account_balance_snapshots(month, scope)
        invoice_history = self.invoice_history(scope)
        latest_invoice_month = invoice_history[0]["invoice_month"] if invoice_history else None
        return {
            "month": month,
            "scope": scope,
            "income_cents": income,
            "expense_cents": expenses,
            "balance_cents": income - expenses,
            "cash_balance_cents": sum(item["amount_cents"] for item in snapshots) if snapshots else None,
            "cash_balance_snapshots": snapshots,
            "card_payment_cents": card_payments,
            "card_purchases_cents": card_purchases,
            "purchase_total_cents": purchase_total,
            "purchase_categories": [
                {"category": name, "amount_cents": amount}
                for name, amount in sorted(purchases_by_category.items(), key=lambda pair: (-pair[1], pair[0]))
            ],
            "latest_invoice_month": latest_invoice_month,
            "expense_categories": [
                {"category": name, "amount_cents": amount}
                for name, amount in sorted(by_category.items(), key=lambda pair: (-pair[1], pair[0]))
            ],
        }


def _description_words(value: str) -> set[str]:
    plain = "".join(char for char in unicodedata.normalize("NFKD", value.lower())
                    if not unicodedata.combining(char))
    return {word for word in re.findall(r"[a-z]{4,}", plain)
            if word not in {"banco", "inter", "pix", "enviado", "recebido", "conta"}}


def _next_month(month: str) -> str:
    year, number = map(int, month.split("-"))
    if number == 12:
        return f"{year + 1:04d}-01"
    return f"{year:04d}-{number + 1:02d}"


def _previous_month(month: str) -> str:
    year, number = map(int, month.split("-"))
    return f"{year - 1:04d}-12" if number == 1 else f"{year:04d}-{number - 1:02d}"


def _month_fully_covered(periods: list[dict], first_day: str, last_day: str) -> bool:
    covered_until = date.fromisoformat(first_day).toordinal() - 1
    for period in sorted(periods, key=lambda row: row["starts_on"]):
        start = date.fromisoformat(period["starts_on"]).toordinal()
        end = date.fromisoformat(period["ends_on"]).toordinal()
        if start > covered_until + 1:
            return False
        covered_until = max(covered_until, end)
        if covered_until >= date.fromisoformat(last_day).toordinal():
            return True
    return False
