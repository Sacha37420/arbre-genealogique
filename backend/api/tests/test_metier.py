"""Règles métier : dates GEDCOM, familles, vivants/décédés, générations, styles
conditionnels, import/export GEDCOM, fusion d'une fiche externe."""
import datetime as dt
from unittest import mock

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import SimpleTestCase

from api.gedcom import parse_date
from api.graph import evaluate_condition, resolve_style
from api.models import (
    DateModifier,
    Event,
    Family,
    FamilyChild,
    FamilySpouse,
    ImportJob,
    Individual,
    NodeStyle,
    PersonalName,
    Place,
    Tree,
)
from api.providers import PersonResult, ProviderError, Relative
from api.providers.base import extract_year, split_name
from api.providers.geocoding import Geocoder
from api.views import _normalize_date

from .helpers import ALICE, FamilyTreeCase, LabTestCase, _Response

D = dt.date


class DatesGedcomTests(SimpleTestCase):
    """La date brute est la seule saisie ; tri et frise reposent sur son analyse."""

    def test_formes_reconnues(self):
        cas = {
            '12 JAN 1875':  ('EXACT', D(1875, 1, 12), D(1875, 1, 12), 'DAY'),
            'jan 1875':     ('EXACT', D(1875, 1, 1), D(1875, 1, 31), 'MONTH'),
            'DEC 1899':     ('EXACT', D(1899, 12, 1), D(1899, 12, 31), 'MONTH'),
            'FEB 1900':     ('EXACT', D(1900, 2, 1), D(1900, 2, 28), 'MONTH'),   # pas bissextile
            'FEB 2000':     ('EXACT', D(2000, 2, 1), D(2000, 2, 29), 'MONTH'),
            '1875':         ('EXACT', D(1875, 1, 1), D(1875, 12, 31), 'YEAR'),
            'ABT 1875':     ('ABOUT', D(1875, 1, 1), D(1875, 12, 31), 'YEAR'),
            'BEF 3 MAR 1901': ('BEFORE', D(1901, 3, 3), D(1901, 3, 3), 'DAY'),
            'AFT 1901':     ('AFTER', D(1901, 1, 1), D(1901, 12, 31), 'YEAR'),
            'BET 1830 AND 1835': ('BETWEEN', D(1830, 1, 1), D(1835, 12, 31), 'YEAR'),
            'FROM 1914 TO NOV 1918': ('PERIOD', D(1914, 1, 1), D(1918, 11, 30), 'YEAR'),
            'FROM 1914':    ('FROM', D(1914, 1, 1), D(1914, 12, 31), 'YEAR'),
            'TO 1918':      ('TO', D(1918, 1, 1), D(1918, 12, 31), 'YEAR'),
            'CAL 1850':     ('CALCULATED', D(1850, 1, 1), D(1850, 12, 31), 'YEAR'),
            # Date impossible : l'année est gardée plutôt que tout perdre.
            '30 FEB 1875':  ('EXACT', D(1875, 1, 1), D(1875, 12, 31), 'YEAR'),
        }
        for brut, (mod, debut, fin, precision) in cas.items():
            with self.subTest(date=brut):
                p = parse_date(brut)
                self.assertEqual((p.start, p.end, p.precision), (debut, fin, precision))
                self.assertEqual(p.modifier, getattr(DateModifier, mod))
                self.assertEqual(p.raw, brut)

    def test_periode_ou_point(self):
        self.assertTrue(parse_date('FROM 1914 TO 1918').is_period)
        self.assertTrue(parse_date('BET 1830 AND 1835').is_period)
        self.assertFalse(parse_date('ABT 1875').is_period)
        self.assertFalse(parse_date('FROM 1914').is_period)

    def test_texte_non_reconnu_garde_sans_date_inventee(self):
        for brut in ('(vers la fin du règne)', 'au printemps', '@#DJULIAN@ 1 JAN 1700',
                     '12/01/1875'):
            with self.subTest(date=brut):
                p = parse_date(brut)
                self.assertIsNone(p.start)
                self.assertTrue(p.phrase)
        self.assertEqual(parse_date('(vers la fin du règne)').phrase, 'vers la fin du règne')

    def test_vide(self):
        for brut in ('', '   ', None):
            p = parse_date(brut)
            self.assertEqual((p.raw, p.start, p.is_period), ('', None, False))

    def test_dates_des_fournisseurs_ramenees_au_gedcom(self):
        self.assertEqual(_normalize_date('1875-03-12'), '12 MAR 1875')
        self.assertEqual(_normalize_date('1875-03'), 'MAR 1875')
        self.assertEqual(_normalize_date('1875'), '1875')
        self.assertEqual(_normalize_date('1875-13-01'), '1875-13-01')   # mois impossible : inchangé
        self.assertEqual(_normalize_date(''), '')
        self.assertEqual(parse_date(_normalize_date('1835-11-30')).start, D(1835, 11, 30))

    def test_outils_des_fournisseurs(self):
        self.assertEqual(extract_year('vers 1875, à Tours'), 1875)
        self.assertIsNone(extract_year('12 mars'))
        self.assertIsNone(extract_year(''))
        self.assertEqual(split_name('Jean Baptiste Dupont'), ('Jean Baptiste', 'Dupont'))
        self.assertEqual(split_name('Madonna'), ('Madonna', ''))
        self.assertEqual(split_name('  '), ('', ''))


class StylesConditionnelsTests(SimpleTestCase):

    def regle(self, priorite, condition, enabled=True, **style):
        return mock.Mock(priority=priorite, enabled=enabled, condition=condition,
                         style=NodeStyle(**style))

    def test_operateurs(self):
        ctx = {'sex': 'F', 'birth_year': 1875, 'surname': 'Dupont', 'has_photo': False,
               'death_year': None, 'custom': {'grade': 'capitaine'}}
        vrai = [
            {'all': [{'field': 'sex', 'op': 'eq', 'value': 'F'}]},
            {'all': [{'field': 'sex', 'op': 'ne', 'value': 'M'}]},
            {'all': [{'field': 'birth_year', 'op': 'lt', 'value': 1900},
                     {'field': 'birth_year', 'op': 'gte', 'value': 1875}]},
            {'any': [{'field': 'sex', 'op': 'eq', 'value': 'M'},
                     {'field': 'surname', 'op': 'contains', 'value': 'pont'}]},
            {'all': [{'field': 'sex', 'op': 'in', 'value': ['F', 'U']}]},
            {'all': [{'field': 'has_photo', 'op': 'exists', 'value': False}]},
            {'all': [{'field': 'custom.grade', 'op': 'eq', 'value': 'capitaine'}]},
        ]
        faux = [
            {},                                                            # règle vide : jamais
            {'all': [{'field': 'sex', 'op': 'eq', 'value': 'M'}]},
            {'all': [{'field': 'sex', 'op': 'eq', 'value': 'F'}],
             'any': [{'field': 'birth_year', 'op': 'gt', 'value': 1900}]},  # all ET any
            {'all': [{'field': 'death_year', 'op': 'lt', 'value': 1900}]},  # inconnu ≠ « avant »
            {'all': [{'field': 'surname', 'op': 'lt', 'value': 1900}]},     # types incompatibles
            {'all': [{'field': 'sex', 'op': 'inconnu', 'value': 'F'}]},
        ]
        for condition in vrai:
            with self.subTest(vrai=condition):
                self.assertTrue(evaluate_condition(condition, ctx))
        for condition in faux:
            with self.subTest(faux=condition):
                self.assertFalse(evaluate_condition(condition, ctx))

    def test_la_regle_la_plus_prioritaire_l_emporte_puis_le_style_force(self):
        toujours = {'all': [{'field': 'sex', 'op': 'eq', 'value': 'F'}]}
        regles = [
            self.regle(20, toujours, text_color='gris'),
            self.regle(10, toujours, text_color='rose', border_color='rose'),
            self.regle(30, toujours, enabled=False, text_color='ignoré'),
            self.regle(40, {'all': [{'field': 'sex', 'op': 'eq', 'value': 'M'}]}, text_color='bleu'),
        ]
        base = NodeStyle(text_color='noir', border_color='noir', accent_color='base')
        style = resolve_style({'sex': 'F'}, regles, base, None)
        # Un style est complet : la règle la plus prioritaire (20) remplace TOUT
        # ce qu'a posé la règle 10, pas seulement la couleur du texte.
        self.assertEqual((style['text_color'], style['border_color']),
                         ('gris', NodeStyle().border_color))
        self.assertNotEqual(style['border_color'], 'rose')
        force = resolve_style({'sex': 'F'}, regles, base, NodeStyle(text_color='forcé'))
        self.assertEqual(force['text_color'], 'forcé')
        self.assertEqual(resolve_style({'sex': 'F'}, [], None, None), {})


class EvenementTests(FamilyTreeCase):

    def creer(self, **corps):
        corps.setdefault('tree', self.tree.pk)
        return self.bob.post('/api/events/', corps, format='json')

    def test_la_date_analysee_vient_du_serveur(self):
        resp = self.creer(individual=self.objects['_mere'].pk, tag='BIRT', date_raw='ABT MAR 1902',
                          date_start='1800-01-01', date_precision='DAY')
        self.assertEqual(resp.status_code, 201, resp.content)
        corps = resp.json()
        self.assertEqual((corps['date_start'], corps['date_end']), ('1902-03-01', '1902-03-31'))
        self.assertEqual((corps['date_precision'], corps['date_modifier']),
                         ('MONTH', DateModifier.ABOUT))
        self.assertEqual(corps['category'], Event.objects.get(pk=corps['id']).category)
        self.assertNotEqual(corps['category'], 'OTHER')            # déduite du tag

    def test_modifier_la_date_la_reanalyse(self):
        event = self.objects['events']
        resp = self.bob.patch(f'/api/events/{event.pk}/', {'date_raw': 'FROM 1914 TO 1918'},
                              format='json')
        self.assertEqual(resp.json()['date_start'], '1914-01-01')
        self.assertTrue(resp.json()['is_period'])

    def test_un_evenement_tient_a_une_personne_ou_a_une_famille(self):
        les_deux = self.creer(individual=self.objects['_mere'].pk,
                              family=self.objects['families'].pk, tag='MARR')
        aucun = self.creer(tag='MARR')
        self.assertEqual((les_deux.status_code, aucun.status_code), (400, 400))

    def test_le_lieu_se_saisit_par_son_nom_dans_l_arbre_de_l_evenement(self):
        mere = self.objects['_mere'].pk
        r1 = self.creer(individual=mere, tag='RESI', place_name='Chinon, Indre-et-Loire')
        r2 = self.creer(individual=mere, tag='OCCU', place_name='Chinon, Indre-et-Loire')
        lieu = Place.objects.get(name='Chinon, Indre-et-Loire')
        self.assertEqual(lieu.tree_id, self.tree.pk)
        self.assertEqual(lieu.hierarchy, ['Chinon', 'Indre-et-Loire'])
        self.assertEqual(r1.json()['place'], r2.json()['place'])      # réutilisé, pas dupliqué
        # Un lieu homonyme d'un autre arbre n'est jamais repris.
        r3 = self.creer(individual=mere, tag='RESI', place_name='Tours, Indre-et-Loire, France')
        self.assertEqual(r3.json()['place'], self.objects['places'].pk)
        vide = self.bob.patch(f"/api/events/{r1.json()['id']}/", {'place_name': ''}, format='json')
        self.assertIsNone(vide.json()['place'])

    def test_un_deces_grise_la_carte_son_retrait_la_ramene(self):
        mere = self.objects['_mere']
        self.assertTrue(mere.is_living)
        deces = self.creer(individual=mere.pk, tag='BURI').json()
        mere.refresh_from_db()
        self.assertFalse(mere.is_living)
        # Requalifié en simple résidence : de nouveau vivante.
        self.bob.patch(f"/api/events/{deces['id']}/", {'tag': 'RESI'}, format='json')
        mere.refresh_from_db()
        self.assertTrue(mere.is_living)
        self.bob.patch(f"/api/events/{deces['id']}/", {'tag': 'DEAT'}, format='json')
        self.bob.delete(f"/api/events/{deces['id']}/")
        mere.refresh_from_db()
        self.assertTrue(mere.is_living)

    def test_une_naissance_ne_change_rien_au_vivant(self):
        self.creer(individual=self.objects['_mere'].pk, tag='BIRT')
        self.assertTrue(Individual.objects.get(pk=self.objects['_mere'].pk).is_living)

    def test_filtre_par_personne_et_catalogue(self):
        pere = self.objects['individuals']
        lus = self.bob.get('/api/events/', {'individual': pere.pk}).json()
        self.assertEqual([e['id'] for e in lus], [self.objects['events'].pk])
        types = {t['tag']: t for t in self.bob.get('/api/events/types/').json()}
        self.assertEqual(types['MARR']['scope'], 'FAMILY')
        self.assertEqual(types['BIRT']['scope'], 'INDIVIDUAL')
        self.assertTrue(types['OCCU']['is_attribute'])


class FamilleTests(FamilyTreeCase):
    """Tout lien de parenté passe par une famille ; une famille qui ne relie
    plus personne disparaît."""

    def setUp(self):
        super().setUp()
        self.pere, self.mere = self.objects['individuals'], self.objects['_mere']
        self.enfant, self.famille = self.objects['_enfant'], self.objects['families']

    def proche(self, de, **corps):
        return self.bob.post(f'/api/individuals/{de.pk}/add_relative/', corps, format='json')

    def test_retirer_un_membre_garde_la_famille_tant_qu_elle_relie_deux_personnes(self):
        self.bob.delete(f"/api/family-children/{self.objects['family-children'].pk}/")
        self.assertTrue(Family.objects.filter(pk=self.famille.pk).exists())      # 2 conjoints
        self.bob.delete(f"/api/family-spouses/{self.objects['family-spouses'].pk}/")
        self.assertFalse(Family.objects.filter(pk=self.famille.pk).exists())     # 1 seul : dissoute
        self.assertTrue(Individual.objects.filter(pk=self.mere.pk).exists())     # les personnes restent

    def test_une_fratrie_sans_parents_est_une_famille(self):
        fratrie = Family.objects.create(tree=self.tree)
        liens = [FamilyChild.objects.create(family=fratrie, individual=p)
                 for p in (self.pere, self.mere, self.enfant)]
        self.bob.delete(f'/api/family-children/{liens[0].pk}/')
        self.assertTrue(Family.objects.filter(pk=fratrie.pk).exists())
        self.bob.delete(f'/api/family-children/{liens[1].pk}/')
        self.assertFalse(Family.objects.filter(pk=fratrie.pk).exists())

    def test_supprimer_une_personne_dissout_les_familles_qu_elle_laisse_vides(self):
        couple = Family.objects.create(tree=self.tree)
        tiers = Individual.objects.create(tree=self.tree)
        FamilySpouse.objects.create(family=couple, individual=self.pere)
        FamilySpouse.objects.create(family=couple, individual=tiers)
        self.assertEqual(self.bob.delete(f'/api/individuals/{self.pere.pk}/').status_code, 204)
        self.assertFalse(Family.objects.filter(pk=couple.pk).exists())           # tiers resté seul
        self.assertTrue(Family.objects.filter(pk=self.famille.pk).exists())      # mère + enfant
        self.assertTrue(Individual.objects.filter(pk=tiers.pk).exists())
        self.assertEqual(Event.objects.filter(individual_id=self.pere.pk).count(), 0)

    def test_un_parent_s_ajoute_a_la_famille_ou_l_on_est_enfant(self):
        resp = self.proche(self.pere, relation='parent', givn='Louis', surn='Martin', sex='M')
        self.assertEqual(resp.status_code, 201, resp.content)
        famille = resp.json()['parent_families'][0]
        self.assertEqual([p['name'] for p in famille['parents']], ['Louis Martin'])
        self.assertEqual(famille['parents'][0]['role'], 'HUSB')                 # déduit du sexe
        self.assertNotEqual(famille['family'], self.famille.pk)
        # Le second parent rejoint la MÊME famille, il n'en crée pas une autre.
        resp = self.proche(self.pere, relation='PARENT', givn='Jeanne', surn='Petit', sex='F')
        self.assertEqual(len(resp.json()['parent_families']), 1)
        self.assertEqual(sorted(p['role'] for p in resp.json()['parent_families'][0]['parents']),
                         ['HUSB', 'WIFE'])

    def test_un_enfant_s_ajoute_a_la_famille_ou_l_on_est_conjoint(self):
        resp = self.proche(self.pere, relation='CHILD', givn='Lucie', pedigree='ADOPTED')
        union = resp.json()['spouse_families'][0]
        self.assertEqual(union['family'], self.famille.pk)
        self.assertEqual(len(union['children']), 2)
        lucie = next(c for c in union['children'] if c['name'].startswith('Lucie'))
        self.assertEqual(lucie['pedigree'], 'ADOPTED')
        # … et la mère, conjointe de cette famille, la voit aussi.
        vue = self.bob.get(f'/api/individuals/{self.mere.pk}/relations/').json()
        self.assertEqual(len(vue['spouse_families'][0]['children']), 2)

    def test_frere_et_conjoint(self):
        resp = self.proche(self.enfant, relation='SIBLING', givn='Anne', pedigree='nimporte')
        fratrie = resp.json()['parent_families'][0]
        self.assertEqual(fratrie['family'], self.famille.pk)
        self.assertEqual([(s['name'], s['pedigree']) for s in fratrie['siblings']],
                         [('Anne', 'BIRTH')])                                     # défaut sûr
        resp = self.proche(self.enfant, relation='SPOUSE', individual=self.mere.pk)
        self.assertEqual(resp.json()['spouse_families'][0]['spouses'][0]['id'], self.mere.pk)

    def test_refus(self):
        cas = [
            {'relation': 'COUSIN', 'givn': 'X'},
            {'relation': 'PARENT'},                                    # ni personne ni nom
            {'relation': 'SPOUSE', 'individual': self.pere.pk},        # soi-même
            {'relation': 'SPOUSE', 'individual': 999999},
        ]
        avant = (Individual.objects.count(), Family.objects.count(), FamilySpouse.objects.count())
        for corps in cas:
            with self.subTest(corps=corps):
                self.assertEqual(self.proche(self.pere, **corps).status_code, 400)
        self.assertEqual((Individual.objects.count(), Family.objects.count(),
                          FamilySpouse.objects.count()), avant)

    def test_ajout_direct_a_une_famille(self):
        vide = self.objects['_famille2']
        r1 = self.bob.post(f'/api/families/{vide.pk}/add_spouse/', {'individual': self.mere.pk},
                           format='json')
        r2 = self.bob.post(f'/api/families/{vide.pk}/add_spouse/',
                           {'individual': self.mere.pk, 'role': 'HUSB'}, format='json')
        self.assertEqual(r1.json()['role'], 'WIFE')
        self.assertEqual(r1.json()['id'], r2.json()['id'])           # pas de doublon, rôle gardé
        self.assertEqual(r2.json()['role'], 'WIFE')
        r3 = self.bob.post(f'/api/families/{vide.pk}/add_child/', {}, format='json')
        self.assertEqual(r3.status_code, 400)

    def test_personne_creee_avec_son_nom(self):
        resp = self.bob.post('/api/individuals/', {
            'tree': self.tree.pk, 'givn': 'Luc', 'surn': 'Martin', 'sex': 'M'}, format='json')
        luc = resp.json()['id']
        nom = PersonalName.objects.get(individual_id=luc)
        self.assertEqual((nom.givn, nom.surn, nom.is_primary), ('Luc', 'Martin', True))
        # Une modification partielle ne touche que ce qui est envoyé.
        self.bob.patch(f'/api/individuals/{luc}/', {'surn': 'Durand'}, format='json')
        self.bob.patch(f'/api/individuals/{luc}/', {'note': 'n'}, format='json')
        nom.refresh_from_db()
        self.assertEqual((nom.givn, nom.surn), ('Luc', 'Durand'))
        self.assertEqual(PersonalName.objects.filter(individual_id=luc).count(), 1)
        self.assertEqual(self.bob.get(f'/api/individuals/{luc}/').json()['full_name'],
                         str(nom))


class GrapheTests(FamilyTreeCase):

    def test_generations_et_liens(self):
        pere, mere, enfant = (self.objects['individuals'], self.objects['_mere'],
                              self.objects['_enfant'])
        petit = Individual.objects.create(tree=self.tree)
        foyer = Family.objects.create(tree=self.tree)
        FamilySpouse.objects.create(family=foyer, individual=enfant)
        FamilyChild.objects.create(family=foyer, individual=petit, pedigree='ADOPTED')
        Event.objects.create(tree=self.tree, family=self.objects['families'], tag='MARR',
                             date_raw='1899')
        graphe = self.carol.get(f'/api/trees/{self.tree.pk}/graph/').json()
        gen = {n['id']: n['generation'] for n in graphe['nodes']}
        self.assertEqual(gen[pere.pk], gen[mere.pk])                 # conjoints : même génération
        self.assertEqual(gen[enfant.pk] - gen[pere.pk], 1)
        self.assertEqual(gen[petit.pk] - gen[pere.pk], 2)
        genres = {(e['source'], e['target']): e['kind'] for e in graphe['edges']}
        f, g = self.objects['families'].pk, foyer.pk
        self.assertEqual(genres[(f'i{pere.pk}', f'f{f}')], 'SPOUSE')
        self.assertEqual(genres[(f'f{f}', f'i{enfant.pk}')], 'PARENT_CHILD')
        self.assertEqual(genres[(f'f{g}', f'i{petit.pk}')], 'ADOPTED')
        jonction = next(x for x in graphe['families'] if x['id'] == f)
        self.assertEqual(jonction['marriage_date'], '1899')
        carte = next(n for n in graphe['nodes'] if n['id'] == pere.pk)
        self.assertEqual((carte['birth_year'], carte['lifespan']), (1900, '1900 – '))

    def test_la_regle_de_style_s_applique_a_la_carte(self):
        from api.models import StyleRule
        rose = NodeStyle.objects.create(tree=self.tree, name='rose', border_color='#rose')
        StyleRule.objects.create(tree=self.tree, name='F', style=rose, priority=5,
                                 condition={'all': [{'field': 'sex', 'op': 'eq', 'value': 'F'}]})
        graphe = self.carol.get(f'/api/trees/{self.tree.pk}/graph/').json()
        styles = {n['id']: n['style'].get('border_color') for n in graphe['nodes']}
        self.assertEqual(styles[self.objects['_mere'].pk], '#rose')
        self.assertNotEqual(styles[self.objects['individuals'].pk], '#rose')

    def test_frise_d_une_vie(self):
        pere, enfant = self.objects['individuals'], self.objects['_enfant']
        Event.objects.create(tree=self.tree, family=self.objects['families'], tag='MARR',
                             **parse_date('1925').as_fields())
        Event.objects.create(tree=self.tree, individual=enfant, tag='BIRT',
                             **parse_date('3 MAY 1930').as_fields())
        Event.objects.filter(pk=self.objects['events'].pk).update(date_start=D(1900, 1, 1))
        frise = self.carol.get(f'/api/individuals/{pere.pk}/timeline/').json()
        self.assertEqual([(e['scope'], e['start_year']) for e in frise['entries']],
                         [('INDIVIDUAL', 1900), ('FAMILY', 1925), ('CHILD', 1930)])
        self.assertEqual(frise['entries'][2]['label'], 'Naissance de Paul Martin')
        self.assertEqual(frise['span'], {'from': 1900, 'to': 1930})


GEDCOM = """0 HEAD
1 GEDC
2 VERS 7.0
0 @F1@ FAM
1 HUSB @I1@
1 WIFE @I2@
1 CHIL @I3@
1 MARR
2 DATE 12 JUN 1860
2 PLAC Tours, France
0 @I1@ INDI
1 NAME Pierre /Lefèvre/
1 SEX M
1 BIRT
2 DATE ABT 1835
2 PLAC Tours, France
1 DEAT
2 DATE 1901
1 OCCU Charpentier
0 @I2@ INDI
1 NAME Jeanne /Morin/
1 SEX F
0 @I3@ INDI
1 NAME Paul /Lefèvre/
1 SEX X
1 NOTE Première ligne
2 CONT seconde ligne
0 @S1@ SOUR
1 TITL Registre paroissial
0 TRLR
"""


class GedcomTests(LabTestCase):

    def setUp(self):
        super().setUp()
        self.alice = self.as_(ALICE)
        self.tree = Tree.objects.create(name='Import', owner_email=ALICE)

    def importer(self, contenu, tree=None):
        return self.alice.post(f'/api/trees/{(tree or self.tree).pk}/import_gedcom/',
                               {'content': contenu, 'filename': 'famille.ged'}, format='json')

    def test_import(self):
        resp = self.importer(GEDCOM)
        self.assertEqual(resp.status_code, 201, resp.content)
        self.assertEqual(resp.json()['status'], 'DONE')
        self.assertEqual(resp.json()['counts'], {'individuals': 3, 'families': 1, 'events': 4,
                                                 'media': 0, 'sources': 1})
        pierre = Individual.objects.get(tree=self.tree, xref_id='@I1@')
        paul = Individual.objects.get(tree=self.tree, xref_id='@I3@')
        self.assertFalse(pierre.is_living)                       # un décès est enregistré
        self.assertTrue(paul.is_living)
        self.assertEqual(paul.sex, 'X' if 'X' in dict(Individual._meta.get_field('sex').choices)
                         else 'U')
        self.assertEqual(paul.note, 'Première ligne\nseconde ligne')
        self.assertEqual(str(pierre.names.get()), 'Pierre Lefèvre')
        naissance = pierre.events.get(tag='BIRT')
        self.assertEqual((naissance.date_start, naissance.date_modifier),
                         (D(1835, 1, 1), DateModifier.ABOUT))
        self.assertEqual(pierre.events.get(tag='OCCU').value, 'Charpentier')
        # La famille est déclarée AVANT ses membres dans le fichier : liens gardés.
        famille = Family.objects.get(tree=self.tree)
        self.assertEqual(sorted(famille.spouses.values_list('role', flat=True)), ['HUSB', 'WIFE'])
        self.assertEqual(famille.children.get().individual, paul)
        self.assertEqual(famille.union_type, 'MARRIED')
        self.assertEqual(Place.objects.filter(tree=self.tree, name='Tours, France').count(), 1)

    def test_export_puis_reimport_donne_le_meme_arbre(self):
        self.importer(GEDCOM)
        export = self.alice.get(f'/api/trees/{self.tree.pk}/gedcom/')
        self.assertEqual(export.status_code, 200)
        self.assertIn('Import.ged', export['Content-Disposition'])
        texte = export.content.decode()
        for ligne in ('0 @I1@ INDI', '1 NAME Pierre /Lefèvre/', '2 DATE ABT 1835', '1 HUSB @I1@',
                      '1 CHIL @I3@', '1 FAMC @F1@', '1 OCCU Charpentier', '0 TRLR'):
            with self.subTest(ligne=ligne):
                self.assertIn(ligne + '\r\n', texte)
        copie = Tree.objects.create(name='Copie', owner_email=ALICE)
        resp = self.importer(texte, copie)
        self.assertEqual(resp.status_code, 201, resp.content)
        self.assertEqual(resp.json()['counts'], {'individuals': 3, 'families': 1, 'events': 4,
                                                 'media': 0, 'sources': 1})
        self.assertEqual(Family.objects.get(tree=copie).children.count(), 1)

    def test_import_en_fichier(self):
        resp = self.alice.post(f'/api/trees/{self.tree.pk}/import_gedcom/', {
            'file': SimpleUploadedFile('f.ged', GEDCOM.encode())}, format='multipart')
        self.assertEqual(resp.status_code, 201)
        self.assertEqual(resp.json()['filename'], 'f.ged')

    def test_import_en_echec_rien_n_est_garde_et_la_trace_le_dit(self):
        casse = GEDCOM.replace('0 @I2@ INDI', '0 @I1@ INDI')        # même identifiant deux fois
        resp = self.importer(casse)
        self.assertEqual(resp.status_code, 400)
        self.assertEqual(Individual.objects.filter(tree=self.tree).count(), 0)   # tout ou rien
        self.assertEqual(Family.objects.filter(tree=self.tree).count(), 0)
        trace = ImportJob.objects.get(tree=self.tree)
        self.assertEqual(trace.status, 'FAILED')
        self.assertTrue(trace.log)

    def test_reimporter_le_meme_fichier_est_refuse_sans_rien_abimer(self):
        # Comportement constaté, pas un défaut : un identifiant GEDCOM (@I1@) est
        # unique dans un arbre, donc un second import du même fichier est refusé
        # en 400 — ni 500, ni doublon, ni import partiel — et tracé comme échec.
        self.assertEqual(self.importer(GEDCOM).status_code, 201)
        avant = (Individual.objects.count(), Family.objects.count(), Event.objects.count(),
                 Place.objects.count())
        resp = self.importer(GEDCOM)
        self.assertEqual(resp.status_code, 400)
        self.assertIn('Import impossible', resp.json()[0])
        self.assertEqual((Individual.objects.count(), Family.objects.count(),
                          Event.objects.count(), Place.objects.count()), avant)
        self.assertEqual(sorted(ImportJob.objects.filter(tree=self.tree)
                                .values_list('status', flat=True)), ['DONE', 'FAILED'])
        # Le même fichier s'importe sans difficulté dans un AUTRE arbre.
        autre = Tree.objects.create(name='Autre', owner_email=ALICE)
        self.assertEqual(self.importer(GEDCOM, autre).status_code, 201)

    def test_contenu_vide_refuse_sans_trace(self):
        self.assertEqual(self.importer('  \n').status_code, 400)
        self.assertFalse(ImportJob.objects.filter(tree=self.tree).exists())

    def test_lignes_illisibles_ignorees(self):
        resp = self.importer('nimporte quoi\n0 @I9@ INDI\n1 NAME Seul /Survivant/\n???\n0 TRLR\n')
        self.assertEqual(resp.json()['counts']['individuals'], 1)


class GeocodageTests(FamilyTreeCase):

    def test_les_lieux_sans_coordonnees_sont_geocodes(self):
        Place.objects.create(tree=self.tree, name='Atlantide')
        Place.objects.create(tree=self.tree, name='Déjà fait', latitude=1, longitude=2)
        reponses = {'Tours, Indre-et-Loire, France': {'latitude': 47.39, 'longitude': 0.69,
                                                      'provider': 'nominatim'}}
        with mock.patch('api.views.Geocoder') as geocoder:
            geocoder.return_value.geocode.side_effect = lambda nom: reponses.get(nom)
            resp = self.bob.post(f'/api/trees/{self.tree.pk}/geocode/',
                                 {'credentials': {'geoapify_key': 'k'}}, format='json')
        self.assertEqual(resp.json(), {'geocoded': 1, 'not_found': 1})
        self.assertEqual(geocoder.call_args[0][0], {'geoapify_key': 'k'})
        self.assertEqual(geocoder.return_value.geocode.call_count, 2)     # pas « Déjà fait »
        tours = Place.objects.get(pk=self.objects['places'].pk)
        self.assertEqual((float(tours.latitude), tours.geocode_provider), (47.39, 'nominatim'))
        self.assertIsNotNone(tours.geocoded_at)
        # Aucun lieu d'un autre arbre n'a été touché.
        self.assertIsNone(Place.objects.get(pk=self.other_objects['places'].pk).latitude)

    def test_erreur_du_geocodeur(self):
        with mock.patch('api.views.Geocoder') as geocoder:
            geocoder.return_value.geocode.side_effect = ProviderError('clé refusée', status=401)
            resp = self.bob.post(f"/api/places/{self.objects['places'].pk}/geocode/", {},
                                 format='json')
        self.assertEqual(resp.status_code, 401)
        self.assertIsNone(Place.objects.get(pk=self.objects['places'].pk).latitude)

    def test_lieu_introuvable(self):
        with mock.patch('api.views.Geocoder') as geocoder:
            geocoder.return_value.geocode.return_value = None
            resp = self.bob.post(f"/api/places/{self.objects['places'].pk}/geocode/", {},
                                 format='json')
        self.assertEqual(resp.status_code, 404)

    def test_le_geocodeur_reel_sans_reseau(self):
        # Le vrai Geocoder, la couche HTTP seule remplacée.
        ok = _Response(200, [{'lat': '47.39', 'lon': '0.69', 'display_name': 'Tours'}])
        with mock.patch('api.providers.geocoding.requests.get', return_value=ok) as get:
            hit = Geocoder({}).geocode('Tours')
        self.assertEqual((hit['latitude'], hit['provider']), (47.39, 'nominatim'))
        self.assertIn('nominatim', get.call_args[0][0])
        with mock.patch('api.providers.geocoding.requests.get', return_value=_Response(403)):
            with self.assertRaises(ProviderError) as refus:
                Geocoder({'geoapify_key': 'k'}).geocode('Tours')
        self.assertEqual(refus.exception.status, 401)
        with self.assertRaises(ProviderError):          # réseau fermé : erreur propre, pas un 500
            Geocoder({}).geocode('Tours')
        self.assertIsNone(Geocoder({}).geocode('   '))


class FusionExterneTests(FamilyTreeCase):
    """L'import d'une fiche externe complète les trous, il n'écrase rien."""

    def importer(self, resultat, **extra):
        fournisseur = mock.Mock()
        fournisseur.fetch.return_value = resultat
        with mock.patch('api.views.get_provider', return_value=fournisseur):
            return self.alice.post('/api/enrich/import/', {
                'provider': 'wikitree', 'external_id': resultat.external_id,
                'tree': self.tree.pk, 'with_photo': False, **extra}, format='json')

    def test_completer_sans_ecraser(self):
        pere = self.objects['individuals']          # Jean Martin, M, né en 1900, sans décès
        resultat = PersonResult(provider='wikitree', external_id='M-1', given_name='Johann',
                                surname='Martinez', sex='F', birth_date='1850-01-01',
                                death_date='1970-05-02', death_place='Lyon',
                                description='Menuisier')
        resp = self.importer(resultat, target=pere.pk)
        self.assertEqual(resp.status_code, 201, resp.content)
        pere.refresh_from_db()
        nom = pere.names.get(is_primary=True)
        self.assertEqual((nom.givn, pere.sex), ('Jean', 'M'))                 # gardés
        self.assertEqual(pere.events.get(tag='BIRT').date_raw, '1900')        # gardée
        self.assertEqual(pere.note, 'Menuisier')                              # trou comblé
        deces = pere.events.get(tag='DEAT')
        self.assertEqual((deces.date_start, deces.place.name), (D(1970, 5, 2), 'Lyon'))
        self.assertFalse(pere.is_living)
        self.assertEqual(Individual.objects.filter(tree=self.tree).count(), 3)   # personne créée

    def test_les_proches_deja_presents_sont_reutilises(self):
        resultat = PersonResult(
            provider='wikitree', external_id='N-1', given_name='Nina', surname='Martin',
            relatives=[
                Relative(relation='FATHER', name=f'Jean Martin{self.tree.pk}', sex='M'),
                Relative(relation='MOTHER', name='Marie Durand', birth_date='1880'),
                Relative(relation='SPOUSE', name='Hugo Blanc', sex='M', death_date='1950'),
                Relative(relation='CHILD', name=''),                                # ignoré
            ])
        resp = self.importer(resultat, with_relatives=True)
        self.assertEqual(resp.status_code, 201, resp.content)
        nina = Individual.objects.get(pk=resp.json()['individual']['id'])
        ids = {r['relation']: r['id'] for r in resp.json()['relatives_created']}
        self.assertEqual(ids['FATHER'], self.objects['individuals'].pk)       # réutilisé
        self.assertEqual(ids['MOTHER'], self.objects['_mere'].pk)             # sans naissance connue
        hugo = Individual.objects.get(pk=ids['SPOUSE'])
        self.assertEqual((hugo.tree_id, hugo.is_living), (self.tree.pk, False))
        self.assertEqual(Individual.objects.filter(tree=self.tree).count(), 3 + 2)
        # Nina devient enfant de la famille de ses parents… ou d'une nouvelle qui les réunit.
        parents = {s.individual_id for s in nina.families_as_child.get().family.spouses.all()}
        self.assertEqual(parents, {ids['FATHER'], ids['MOTHER']})
        self.assertEqual(nina.families_as_spouse.get().family.spouses.count(), 2)
