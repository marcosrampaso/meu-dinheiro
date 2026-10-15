import tempfile
import unittest
from pathlib import Path

from finance import FinanceStore
from transaction_review import apply_review, review_overview


class LocalLearningTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.store = FinanceStore(Path(self.directory.name)/'fixture.db')

    def purchase(self, amount, category='Importado', description='Autoposto exemplo', scope='personal'):
        return self.store.add({'kind':'expense','description':description,'amount':amount,
            'category':category,'occurred_on':'2026-10-10','scope':scope})

    def test_same_station_learns_different_categories_by_amount_without_global_rule(self):
        for amount,category in [('28','Tabaco'),('35','Tabaco'),('150','Transporte'),('180','Transporte')]:
            self.purchase(amount, category)
        low,high,uncertain = self.purchase('32'),self.purchase('160'),self.purchase('80')
        self.assertEqual(self.store.remembered_categories([low,high]), {})
        predictions = self.store.local_category_suggestions([low,high,uncertain])
        self.assertEqual(predictions[low['id']]['category'], 'Tabaco')
        self.assertEqual(predictions[high['id']]['category'], 'Transporte')
        self.assertEqual(predictions[low['id']]['source'], 'knn')
        self.assertNotIn(uncertain['id'], predictions)
        self.assertEqual(self.store.get(low['id'])['category'], 'Importado')

    def test_one_small_purchase_does_not_categorize_fuel_and_scopes_never_mix(self):
        self.purchase('30','Tabaco')
        fuel = self.purchase('150')
        other_scope = self.purchase('30',scope='business')
        self.assertEqual(self.store.local_category_suggestions([fuel,other_scope]), {})
        self.purchase('32','Tabaco')
        unrelated = self.purchase('31',description='Farmácia distante')
        generic = self.purchase('31',description='PIX')
        self.assertEqual(self.store.local_category_suggestions([unrelated,generic]), {})

    def test_knn_matches_near_descriptions_but_amount_alone_is_insufficient(self):
        self.purchase('30','Tabaco',description='Autoposto exemplo centro')
        self.purchase('32','Tabaco',description='Autoposto exemplo centro')
        near = self.purchase('31',description='Autoposto exemplo centro bra')
        prediction = self.store.local_category_suggestions([near])[near['id']]
        self.assertEqual((prediction['source'],prediction['category']), ('knn','Tabaco'))

    def test_correction_replaces_training_label_and_no_remember_removes_sample(self):
        first = self.purchase('30','Transporte')
        self.store.update(first['id'],{**first,'amount':'30','category':'Tabaco','excluded':False})
        fresh = self.purchase('32')
        self.assertEqual(self.store.remembered_categories([fresh]), {fresh['id']:'Tabaco'})
        with self.store._connect() as c:
            self.assertEqual(c.execute('SELECT COUNT(*) FROM category_examples').fetchone()[0],1)
        row = next(r for r in review_overview(self.store)['items'] if r['id']==first['id'])
        apply_review(self.store,{'items':[{'id':row['id'],'category':'Transporte','signature':row['signature']}], 'remember':False})
        with self.store._connect() as c:
            self.assertEqual(c.execute('SELECT COUNT(*) FROM category_examples').fetchone()[0],0)

    def test_custom_manual_category_is_scoped_available_and_can_be_applied(self):
        self.purchase('30','Comida de rua',description='Banca exemplo')
        fresh = self.purchase('32',description='Banca exemplo')
        self.assertIn('Comida de rua',self.store.purchase_category_choices('personal'))
        self.assertNotIn('Comida de rua',self.store.purchase_category_choices('business'))
        self.assertEqual(self.store.remembered_categories([fresh]),{fresh['id']:'Comida de rua'})
        self.store.apply_purchase_categories([{'id':fresh['id'],'category':'Comida de rua'}])
        self.assertEqual(self.store.get(fresh['id'])['category'],'Comida de rua')

    def test_legacy_seed_once_and_automatic_classifications_do_not_train_themselves(self):
        original = self.purchase('30','Tabaco')
        with self.store._connect() as c:
            c.execute('DROP TABLE category_examples')
        reopened = FinanceStore(self.store.path)
        fresh = self.purchase('32')
        self.assertEqual(reopened.apply_remembered_categories(),1)
        reopened = FinanceStore(self.store.path)
        with reopened._connect() as c:
            self.assertEqual(c.execute('SELECT COUNT(*) FROM category_examples').fetchone()[0],1)
        self.store.delete(original['id'])
        with self.store._connect() as c:
            self.assertEqual(c.execute('SELECT COUNT(*) FROM category_examples').fetchone()[0],0)
