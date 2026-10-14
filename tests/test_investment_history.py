import tempfile
import unittest
from pathlib import Path

from finance import FinanceStore
from investment_history import capture_observation, daily_observations, received_income
from manual_investments import save_manual_flow, save_manual_snapshot
from performance import performance_overview


class InvestmentHistoryTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.store = FinanceStore(Path(self.directory.name) / 'test.db')

    def observe(self, day, total, time='18:00:00'):
        with self.store._connect() as c:
            capture_observation(c, {'as_of': day, 'checked_at': day+'T'+time+'Z',
                'assets_cents': total-1000, 'cash_cents': 1000, 'total_cents': total,
                'assets': [{'name': 'Fundo Exemplo', 'category': 'Fundos de Investimentos',
                            'value_cents': total-1000, 'quote_date': day}]})

    def movements(self):
        with self.store._connect() as c:
            for key, day, label, cents in [
                ('a'*64, '2026-10-06', 'Pix recebido de Exemplo', 10000),
                ('b'*64, '2026-10-07', 'Transferência para conta investimento', -5000),
                ('c'*64, '2026-10-08', 'Transferência de dividendos Exemplo11', 1000),
                ('d'*64, '2026-10-09', 'Pix enviado para Exemplo', -5000),
            ]:
                c.execute('INSERT INTO investment_entries (import_key,occurred_at,description,amount_cents,balance_cents) VALUES (?,?,?,?,0)',
                          (key, day+'T12:00:00', label, cents))
            c.execute("INSERT INTO authoritative_periods(source,scope,starts_on,ends_on) VALUES ('XP','personal','2026-10-01','2026-10-11')")

    def cdi(self):
        self.store.save_market_quotes([{'source': 'bcb', 'identifier': '12', 'quote_date': day,
                                      'price': '0.05', 'checked_at': '2026-10-12T00:00:00Z'}
            for day in ('2026-10-02','2026-10-05','2026-10-06','2026-10-07','2026-10-08','2026-10-09')])

    def test_every_sync_is_retained_but_same_day_is_one_chart_point(self):
        self.observe('2026-10-01', 100000)
        self.observe('2026-10-01', 100100, '21:00:00')
        with self.store._connect() as c:
            self.assertEqual(c.execute('SELECT COUNT(*) FROM investment_observations').fetchone()[0], 2)
            daily = daily_observations(c)
        self.assertEqual(len(daily), 1)
        self.assertEqual(daily[0]['total_cents'], 100100)
        result = performance_overview(self.store)
        self.assertEqual(result['basis'], 'pluggy')
        self.assertEqual(result['status'], 'needs_snapshots')
        self.assertEqual(result['baseline']['as_of'], '2026-10-01')
        self.assertNotIn('portfolio_percent', result)

    def test_return_adjusts_flows_and_counts_paid_dividend_once(self):
        self.observe('2026-10-01', 100000)
        self.observe('2026-10-11', 106000)
        self.movements()
        self.cdi()
        save_manual_flow(self.store, {'occurred_on': '2026-10-06', 'kind': 'deposit', 'amount': '100,00'})
        result = performance_overview(self.store)
        self.assertEqual(result['status'], 'ready')
        self.assertEqual(result['gain_cents'], 1000)
        self.assertEqual(result['received_income_cents'], 1000)
        self.assertEqual(result['intervals'][0]['external_flow_cents'], 5000)
        self.assertAlmostEqual(result['portfolio_percent'], 0.9615, places=3)
        self.assertIsNotNone(result['cdi_percent'])

    def test_missing_cdi_does_not_hide_portfolio_gain(self):
        self.observe('2026-10-01', 100000)
        self.observe('2026-10-11', 106000)
        self.movements()
        result = performance_overview(self.store)
        self.assertEqual(result['status'], 'ready')
        self.assertEqual(result['benchmark_status'], 'needs_cdi')
        self.assertEqual(result['gain_cents'], 1000)
        self.assertIsNone(result['cdi_percent'])
        self.assertTrue(all(p['cdi_percent'] is None for p in result['points']))

    def test_incomplete_movement_coverage_blocks_unverified_return(self):
        self.observe('2026-10-01', 100000)
        self.observe('2026-10-11', 106000)
        self.movements()
        with self.store._connect() as c:
            c.execute("UPDATE authoritative_periods SET ends_on='2026-10-09'")
        self.assertEqual(performance_overview(self.store)['status'], 'needs_movements')

    def test_manual_full_values_are_not_mixed_with_assets_cash_history(self):
        self.observe('2026-10-11', 100000)
        save_manual_snapshot(self.store, {'as_of': '2026-10-01', 'total': '500,00'})
        save_manual_snapshot(self.store, {'as_of': '2026-10-11', 'total': '1500,00'})
        self.assertEqual(performance_overview(self.store)['snapshot_count'], 1)
        manual = performance_overview(self.store, 'total')
        self.assertEqual(manual['snapshot_count'], 2)
        self.assertEqual(manual['latest_snapshot']['total_cents'], 150000)

    def test_distribution_label_does_not_mistake_redemption_or_pix_sender(self):
        self.assertTrue(received_income('Rendimento Exemplo11', 1000))
        self.assertFalse(received_income('Resgate com rendimento', 100000))
        self.assertFalse(received_income('Pix recebido de Rendimento Exemplo', 1000))
        self.assertFalse(received_income('Dividendo estornado', -1000))


if __name__ == '__main__':
    unittest.main()
