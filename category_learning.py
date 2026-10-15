"""Local reviewed examples and conservative nearest-neighbor purchase suggestions."""

from collections import Counter, defaultdict
import math

from finance import PURCHASE_CATEGORIES, merchant_key


def learnable(category):
    if not isinstance(category, str) or not 1 <= len(category.strip()) <= 60:
        return False
    # Custom purchase categories are valid labels; financial movements are not purchases.
    key = merchant_key(category)
    return key not in {'importado', 'sem categoria', 'pagamento de fatura', 'antecipacao da fatura',
        'transferencia para investimento', 'movimento de investimento', 'transferencia entre contas',
        'entrada desconsiderada', 'cashback', 'estorno', 'estorno de pix', 'reembolso'}


def choices(connection, scope):
    categories = {r[0] for r in connection.execute(
        "SELECT DISTINCT category FROM category_examples WHERE (?='all' OR scope=?)", (scope,scope))}
    return sorted(set(PURCHASE_CATEGORIES) | categories)


def example_key(row):
    return 'import:' + row['import_key'] if row.get('import_key') else 'manual:' + str(row['id'])


def record_example(connection, identifier, learn=True):
    row = connection.execute('SELECT * FROM transactions WHERE id=?', (identifier,)).fetchone()
    if row is None:
        return
    row = dict(row)
    key = example_key(row)
    if not learn or row['kind'] != 'expense' or row['excluded'] or row.get('pending') or not learnable(row['category']):
        connection.execute('DELETE FROM category_examples WHERE example_key=?', (key,))
        return
    merchant = merchant_key(row['description'])
    if not merchant:
        connection.execute('DELETE FROM category_examples WHERE example_key=?', (key,))
        return
    connection.execute('''INSERT INTO category_examples
        (example_key,scope,merchant_key,description,amount_cents,payment_type,category)
        VALUES (?,?,?,?,?,?,?) ON CONFLICT(example_key) DO UPDATE SET
        scope=excluded.scope,merchant_key=excluded.merchant_key,description=excluded.description,
        amount_cents=excluded.amount_cents,payment_type=excluded.payment_type,category=excluded.category,
        updated_at=CURRENT_TIMESTAMP''',
        (key,row['scope'],merchant,row['description'],row['amount_cents'],
         'credit' if row['invoice_month'] else 'account',row['category']))


def initialize(connection):
    exists = connection.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='category_examples'").fetchone()
    connection.execute('''CREATE TABLE IF NOT EXISTS category_examples (
        example_key TEXT PRIMARY KEY, scope TEXT NOT NULL, merchant_key TEXT NOT NULL,
        description TEXT NOT NULL, amount_cents INTEGER NOT NULL CHECK(amount_cents>0),
        payment_type TEXT NOT NULL, category TEXT NOT NULL,
        updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP)''')
    connection.execute('CREATE INDEX IF NOT EXISTS category_examples_scope ON category_examples(scope,merchant_key)')
    if not exists:
        # Previous releases treated saved purchase categories as approved. Snapshot once;
        # future provider imports and unapproved predictions never become training labels.
        for row in connection.execute("SELECT id FROM transactions WHERE kind='expense' AND excluded=0").fetchall():
            record_example(connection, row['id'])


def _near_amount(left, right):
    return abs(left-right) <= max(1000, .35 * right)


def exact_categories(connection, rows):
    examples = defaultdict(list)
    for row in connection.execute('SELECT * FROM category_examples'):
        examples[(row['scope'],row['merchant_key'])].append(dict(row))
    memory = {(r['scope'],r['merchant_key']):r['category'] for r in connection.execute('SELECT * FROM category_memory')}
    result = {}
    for row in rows:
        key = (row['scope'],merchant_key(row['description']))
        category = memory.get(key)
        samples = examples.get(key, [])
        # Conflicting categories need amount context and human review, never a
        # merchant-wide automatic rule. Do not extrapolate a small purchase to fuel.
        if category and (not samples or ({r['category'] for r in samples} == {category} and
                (not row.get('amount_cents') or any(_near_amount(row['amount_cents'],r['amount_cents']) for r in samples)))):
            result[row['id']] = category
    return result


def _grams(text):
    text = ' ' + text + ' '
    return {text[i:i+3] for i in range(len(text)-2)}


def _similarity(left, right):
    if left == right:
        return 1.0
    a,b = set(left.split()),set(right.split())
    ga,gb = _grams(left),_grams(right)
    return .6 * len(a & b)/max(1,len(a | b)) + .4 * 2 * len(ga & gb)/max(1,len(ga)+len(gb))


def suggest_local(connection, rows):
    """Weighted kNN (k<=3). Agreement is a vote score, not a probability."""
    exact = exact_categories(connection, rows)
    examples = [dict(r) for r in connection.execute('SELECT * FROM category_examples')]
    result = {}
    for row in rows:
        if row['id'] in exact:
            result[row['id']] = {'category':exact[row['id']], 'source':'learned',
                'reason':'Descrição já confirmada por você e valor próximo aos exemplos salvos.'}
            continue
        key = merchant_key(row['description'])
        if not key or not row.get('amount_cents'):
            continue
        candidates = []
        for sample in examples:
            if sample['scope'] != row['scope'] or sample['example_key'] == example_key(row):
                continue
            similarity = _similarity(key, sample['merchant_key'])
            amount_distance = abs(math.log2((row['amount_cents']+100)/(sample['amount_cents']+100)))
            # Amount alone cannot connect unrelated merchants; remote extrapolation abstains.
            if similarity < .55 or amount_distance > .85:
                continue
            payment = 'credit' if row.get('invoice_month') else 'account'
            distance = .6*(1-similarity) + .35*amount_distance/2 + .05*(payment != sample['payment_type'])
            candidates.append((distance,sample))
        nearest = sorted(candidates, key=lambda pair:(pair[0],pair[1]['example_key']))[:3]
        if len(nearest) < 2:
            continue
        votes = Counter()
        counts = Counter()
        for distance,sample in nearest:
            votes[sample['category']] += 1/(.05+distance)
            counts[sample['category']] += 1
        category,weight = votes.most_common(1)[0]
        agreement = weight/sum(votes.values())
        if counts[category] < 2 or agreement < .8:
            continue
        result[row['id']] = {'category':category,'source':'knn','agreement':round(agreement,3),
            'neighbor_count':len(nearest), 'reason':f"kNN local: {counts[category]} de {len(nearest)} exemplos próximos em descrição e valor concordam. Revise a sugestão."}
    return result
