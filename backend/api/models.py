"""
Modèle de données généalogique.

Le socle suit la spécification FamilySearch GEDCOM 7.0 (https://gedcom.io) :
  INDI (Individual) · FAM (Family) · SOUR (Source) · REPO (Repository)
  OBJE (MediaObject) · SNOTE (SharedNote) · ASSO (Association)

Il est étendu avec ce dont GEDCOM ne parle pas mais dont un éditeur d'arbre a
besoin : positions des nœuds, styles, règles de style conditionnelles, gabarits
de cartes d'identité, réglages de vue, champs personnalisés et résultats
d'enrichissement issus des fournisseurs externes.

Les photos sont stockées **en base** (MediaObject.data, BinaryField) et non sur
le disque : l'application n'a donc pas de volume persistant à gérer.
"""
import uuid

from django.db import models


# ─────────────────────────────────────────────────────────────────────────────
# Énumérations GEDCOM 7
# ─────────────────────────────────────────────────────────────────────────────

class Sex(models.TextChoices):
    MALE = 'M', 'Masculin'
    FEMALE = 'F', 'Féminin'
    OTHER = 'X', 'Autre / intersexe'
    UNKNOWN = 'U', 'Inconnu'


class NameType(models.TextChoices):
    BIRTH = 'BIRTH', 'Nom de naissance'
    MARRIED = 'MARRIED', 'Nom d’épouse/époux'
    MAIDEN = 'MAIDEN', 'Nom de jeune fille'
    AKA = 'AKA', 'Aussi connu comme'
    IMMIGRANT = 'IMMIGRANT', 'Nom d’immigration'
    PROFESSIONAL = 'PROFESSIONAL', 'Nom professionnel'
    RELIGIOUS = 'RELIGIOUS', 'Nom religieux'
    OTHER = 'OTHER', 'Autre'


class Pedigree(models.TextChoices):
    """GEDCOM 7 : FAMC.PEDI — nature du lien enfant → famille."""

    BIRTH = 'BIRTH', 'Biologique'
    ADOPTED = 'ADOPTED', 'Adopté'
    FOSTER = 'FOSTER', 'Famille d’accueil'
    STEP = 'STEP', 'Beau-parent'
    SEALING = 'SEALING', 'Scellement (LDS)'
    OTHER = 'OTHER', 'Autre'


class ChildStatus(models.TextChoices):
    """GEDCOM 7 : FAMC.STAT — fiabilité du lien de filiation."""

    PROVEN = 'PROVEN', 'Prouvé'
    CHALLENGED = 'CHALLENGED', 'Contesté'
    DISPROVEN = 'DISPROVEN', 'Réfuté'


class UnionType(models.TextChoices):
    MARRIED = 'MARRIED', 'Mariage'
    CIVIL = 'CIVIL', 'Union civile / PACS'
    PARTNERS = 'PARTNERS', 'Union libre'
    UNKNOWN = 'UNKNOWN', 'Non précisé'


class SpouseRole(models.TextChoices):
    """HUSB / WIFE dans GEDCOM ; PARTNER couvre les unions non genrées."""

    HUSBAND = 'HUSB', 'Époux'
    WIFE = 'WIFE', 'Épouse'
    PARTNER = 'PARTNER', 'Partenaire'


class EventTag(models.TextChoices):
    """
    Événements et attributs GEDCOM 7.

    Les tags marqués (attr) portent une valeur textuelle dans Event.value
    (ex. OCCU = « Charpentier »). Le tag générique EVEN / FACT accepte un
    libellé libre dans Event.custom_type : c'est le point d'extension pour tout
    événement que la spécification ne prévoit pas.
    """

    # ── Événements individuels ────────────────────────────────────────────
    BIRT = 'BIRT', 'Naissance'
    CHR = 'CHR', 'Baptême (enfant)'
    DEAT = 'DEAT', 'Décès'
    BURI = 'BURI', 'Inhumation'
    CREM = 'CREM', 'Crémation'
    ADOP = 'ADOP', 'Adoption'
    BAPM = 'BAPM', 'Baptême'
    BARM = 'BARM', 'Bar-mitsvah'
    BASM = 'BASM', 'Bat-mitsvah'
    BLES = 'BLES', 'Bénédiction'
    CONF = 'CONF', 'Confirmation'
    FCOM = 'FCOM', 'Première communion'
    ORDN = 'ORDN', 'Ordination'
    NATU = 'NATU', 'Naturalisation'
    EMIG = 'EMIG', 'Émigration'
    IMMI = 'IMMI', 'Immigration'
    CENS = 'CENS', 'Recensement'
    PROB = 'PROB', 'Homologation'
    WILL = 'WILL', 'Testament'
    GRAD = 'GRAD', 'Diplôme'
    RETI = 'RETI', 'Retraite'

    # ── Attributs individuels (porteurs d'une valeur) ─────────────────────
    CAST = 'CAST', 'Caste'
    DSCR = 'DSCR', 'Description physique'
    EDUC = 'EDUC', 'Éducation'
    IDNO = 'IDNO', 'Numéro d’identification'
    NATI = 'NATI', 'Nationalité'
    NCHI = 'NCHI', 'Nombre d’enfants'
    NMR = 'NMR', 'Nombre de mariages'
    OCCU = 'OCCU', 'Profession'
    PROP = 'PROP', 'Propriété'
    RELI = 'RELI', 'Religion'
    RESI = 'RESI', 'Résidence'
    SSN = 'SSN', 'Numéro de sécurité sociale'
    TITL = 'TITL', 'Titre de noblesse'

    # ── Événements familiaux ──────────────────────────────────────────────
    MARR = 'MARR', 'Mariage'
    MARB = 'MARB', 'Bans du mariage'
    MARC = 'MARC', 'Contrat de mariage'
    MARL = 'MARL', 'Licence de mariage'
    MARS = 'MARS', 'Règlement matrimonial'
    ENGA = 'ENGA', 'Fiançailles'
    DIV = 'DIV', 'Divorce'
    DIVF = 'DIVF', 'Demande de divorce'
    ANUL = 'ANUL', 'Annulation'
    SEPA = 'SEPA', 'Séparation'

    # ── Extension libre ───────────────────────────────────────────────────
    EVEN = 'EVEN', 'Événement personnalisé'
    FACT = 'FACT', 'Fait personnalisé'


#: Tags GEDCOM portant une valeur (attributs) plutôt qu'un simple fait daté.
ATTRIBUTE_TAGS = {
    EventTag.CAST, EventTag.DSCR, EventTag.EDUC, EventTag.IDNO, EventTag.NATI,
    EventTag.NCHI, EventTag.NMR, EventTag.OCCU, EventTag.PROP, EventTag.RELI,
    EventTag.RESI, EventTag.SSN, EventTag.TITL, EventTag.FACT,
}

#: Tags applicables à une famille (FAM) et non à un individu (INDI).
FAMILY_TAGS = {
    EventTag.MARR, EventTag.MARB, EventTag.MARC, EventTag.MARL, EventTag.MARS,
    EventTag.ENGA, EventTag.DIV, EventTag.DIVF, EventTag.ANUL, EventTag.SEPA,
}


class EventCategory(models.TextChoices):
    """Regroupe les événements en bandes colorées sur la frise chronologique."""

    LIFE = 'LIFE', 'Vie'
    FAMILY = 'FAMILY', 'Famille'
    EDUCATION = 'EDUCATION', 'Éducation'
    WORK = 'WORK', 'Travail'
    MILITARY = 'MILITARY', 'Militaire'
    RESIDENCE = 'RESIDENCE', 'Résidence'
    RELIGION = 'RELIGION', 'Religion'
    HEALTH = 'HEALTH', 'Santé'
    LEGAL = 'LEGAL', 'Juridique'
    MIGRATION = 'MIGRATION', 'Migration'
    OTHER = 'OTHER', 'Autre'


class DateModifier(models.TextChoices):
    """GEDCOM 7 : formes que peut prendre une valeur DATE."""

    EXACT = 'EXACT', 'Date exacte'
    ABOUT = 'ABT', 'Vers (ABT)'
    CALCULATED = 'CAL', 'Calculée (CAL)'
    ESTIMATED = 'EST', 'Estimée (EST)'
    BEFORE = 'BEF', 'Avant (BEF)'
    AFTER = 'AFT', 'Après (AFT)'
    BETWEEN = 'BET', 'Entre … et … (BET/AND)'
    FROM = 'FROM', 'À partir de (FROM)'
    TO = 'TO', 'Jusqu’à (TO)'
    PERIOD = 'PERIOD', 'Période (FROM … TO …)'
    PHRASE = 'PHRASE', 'Texte libre'
    UNKNOWN = 'UNKNOWN', 'Inconnue'


class Calendar(models.TextChoices):
    GREGORIAN = 'GREGORIAN', 'Grégorien'
    JULIAN = 'JULIAN', 'Julien'
    FRENCH_R = 'FRENCH_R', 'Républicain'
    HEBREW = 'HEBREW', 'Hébraïque'


class DatePrecision(models.TextChoices):
    DAY = 'DAY', 'Jour'
    MONTH = 'MONTH', 'Mois'
    YEAR = 'YEAR', 'Année'
    NONE = 'NONE', 'Aucune'


# ─────────────────────────────────────────────────────────────────────────────
# Utilisateur
# ─────────────────────────────────────────────────────────────────────────────

class UserRecord(models.Model):
    """Utilisateur Keycloak, créé automatiquement à la première connexion."""

    email = models.EmailField(primary_key=True, max_length=255)
    display_name = models.CharField(max_length=200, blank=True)
    registered_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'user_records'
        ordering = ['email']

    def __str__(self) -> str:
        return self.display_name or self.email


# ─────────────────────────────────────────────────────────────────────────────
# Arbre — racine de tout (un utilisateur peut en avoir plusieurs)
# ─────────────────────────────────────────────────────────────────────────────

class Tree(models.Model):
    """Un arbre généalogique. Équivaut à un fichier GEDCOM complet."""

    name = models.CharField(max_length=200)
    description = models.TextField(blank=True)
    owner_email = models.EmailField(max_length=255, db_index=True)
    is_public = models.BooleanField(default=False)

    # En-tête GEDCOM (HEAD), conservé pour un export fidèle
    gedcom_version = models.CharField(max_length=10, default='7.0')
    source_software = models.CharField(max_length=100, default='arbre-genealogique')
    submitter_name = models.CharField(max_length=200, blank=True)
    copyright = models.CharField(max_length=250, blank=True)
    language = models.CharField(max_length=15, default='fr')
    note = models.TextField(blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'trees'
        ordering = ['-updated_at']

    def __str__(self) -> str:
        return self.name


# ─────────────────────────────────────────────────────────────────────────────
# Lieux (PLAC) — géocodables pour la carte et la frise
# ─────────────────────────────────────────────────────────────────────────────

class Place(models.Model):
    tree = models.ForeignKey(Tree, on_delete=models.CASCADE, related_name='places')
    name = models.CharField(max_length=300)
    #: Découpage hiérarchique GEDCOM (PLAC.FORM) : ["Paris", "Seine", "France"]
    hierarchy = models.JSONField(default=list, blank=True)
    latitude = models.DecimalField(max_digits=9, decimal_places=6, null=True, blank=True)
    longitude = models.DecimalField(max_digits=9, decimal_places=6, null=True, blank=True)
    geocode_provider = models.CharField(max_length=50, blank=True)
    geocoded_at = models.DateTimeField(null=True, blank=True)
    note = models.TextField(blank=True)

    class Meta:
        db_table = 'places'
        ordering = ['name']
        constraints = [
            models.UniqueConstraint(fields=['tree', 'name'], name='uniq_place_per_tree'),
        ]

    def __str__(self) -> str:
        return self.name


# ─────────────────────────────────────────────────────────────────────────────
# Individus (INDI)
# ─────────────────────────────────────────────────────────────────────────────

class Individual(models.Model):
    tree = models.ForeignKey(Tree, on_delete=models.CASCADE, related_name='individuals')
    #: Identifiant GEDCOM (@I1@) — stable à travers les imports/exports
    xref_id = models.CharField(max_length=30, blank=True)
    uid = models.UUIDField(default=uuid.uuid4, editable=False)

    sex = models.CharField(max_length=1, choices=Sex.choices, default=Sex.UNKNOWN)
    #: Passe à faux dès qu'un décès est enregistré ; sert à masquer les vivants.
    is_living = models.BooleanField(default=True)
    confidential = models.BooleanField(default=False)

    note = models.TextField(blank=True)
    #: Valeurs des champs personnalisés (voir CustomFieldDef) : {"clé": valeur}
    custom = models.JSONField(default=dict, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'individuals'
        ordering = ['id']
        constraints = [
            models.UniqueConstraint(fields=['tree', 'xref_id'], name='uniq_indi_xref_per_tree'),
        ]
        indexes = [models.Index(fields=['tree', 'sex'])]

    def __str__(self) -> str:
        name = self.names.first()
        return str(name) if name else f'Individu #{self.pk}'


class PersonalName(models.Model):
    """
    NAME et ses sous-structures GEDCOM 7.

    Un individu peut porter plusieurs noms (naissance, mariage, alias…) ;
    celui marqué is_primary alimente les cartes d'identité.
    """

    individual = models.ForeignKey(Individual, on_delete=models.CASCADE, related_name='names')
    type = models.CharField(max_length=15, choices=NameType.choices, default=NameType.BIRTH)

    npfx = models.CharField('Préfixe', max_length=60, blank=True)      # Dr, Sir
    givn = models.CharField('Prénom(s)', max_length=120, blank=True)   # GIVN
    nick = models.CharField('Surnom', max_length=60, blank=True)       # NICK
    spfx = models.CharField('Particule', max_length=30, blank=True)    # de, van
    surn = models.CharField('Nom', max_length=120, blank=True)         # SURN
    nsfx = models.CharField('Suffixe', max_length=30, blank=True)      # Jr, III

    is_primary = models.BooleanField(default=True)
    order = models.PositiveSmallIntegerField(default=0)

    class Meta:
        db_table = 'personal_names'
        ordering = ['-is_primary', 'order', 'id']

    def __str__(self) -> str:
        parts = [p for p in (self.givn, self.spfx, self.surn) if p]
        return ' '.join(parts) or '(sans nom)'

    @property
    def gedcom_value(self) -> str:
        """Forme GEDCOM du NAME : « Jean /Dupont/ »."""
        surname = ' '.join(p for p in (self.spfx, self.surn) if p)
        return f'{self.givn} /{surname}/'.strip()


# ─────────────────────────────────────────────────────────────────────────────
# Familles (FAM) — seul porteur des liens entre individus, comme en GEDCOM
# ─────────────────────────────────────────────────────────────────────────────

class Family(models.Model):
    tree = models.ForeignKey(Tree, on_delete=models.CASCADE, related_name='families')
    xref_id = models.CharField(max_length=30, blank=True)
    union_type = models.CharField(max_length=10, choices=UnionType.choices, default=UnionType.UNKNOWN)
    note = models.TextField(blank=True)
    custom = models.JSONField(default=dict, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'families'
        ordering = ['id']
        constraints = [
            models.UniqueConstraint(fields=['tree', 'xref_id'], name='uniq_fam_xref_per_tree'),
        ]

    def __str__(self) -> str:
        return f'Famille #{self.pk}'


class FamilySpouse(models.Model):
    """HUSB / WIFE — un conjoint dans une famille (FAMS côté individu)."""

    family = models.ForeignKey(Family, on_delete=models.CASCADE, related_name='spouses')
    individual = models.ForeignKey(Individual, on_delete=models.CASCADE, related_name='families_as_spouse')
    role = models.CharField(max_length=8, choices=SpouseRole.choices, default=SpouseRole.PARTNER)
    order = models.PositiveSmallIntegerField(default=0)

    class Meta:
        db_table = 'family_spouses'
        ordering = ['order', 'id']
        constraints = [
            models.UniqueConstraint(fields=['family', 'individual'], name='uniq_spouse_per_family'),
        ]


class FamilyChild(models.Model):
    """CHIL — un enfant dans une famille (FAMC côté individu)."""

    family = models.ForeignKey(Family, on_delete=models.CASCADE, related_name='children')
    individual = models.ForeignKey(Individual, on_delete=models.CASCADE, related_name='families_as_child')
    pedigree = models.CharField(max_length=10, choices=Pedigree.choices, default=Pedigree.BIRTH)
    status = models.CharField(max_length=12, choices=ChildStatus.choices, blank=True)
    order = models.PositiveSmallIntegerField(default=0)

    class Meta:
        db_table = 'family_children'
        ordering = ['order', 'id']
        constraints = [
            models.UniqueConstraint(fields=['family', 'individual'], name='uniq_child_per_family'),
        ]


class Association(models.Model):
    """
    ASSO — lien non familial entre deux individus (parrain, témoin, ami…).
    Représente ce que HUSB/WIFE/CHIL ne couvrent pas.
    """

    individual = models.ForeignKey(Individual, on_delete=models.CASCADE, related_name='associations')
    associate = models.ForeignKey(Individual, on_delete=models.CASCADE, related_name='associated_with')
    role = models.CharField(max_length=30, default='OTHER')  # GODP, WITN, FRIEND, NGHBR, OFFICIATOR…
    custom_role = models.CharField(max_length=100, blank=True)
    note = models.TextField(blank=True)

    class Meta:
        db_table = 'associations'
        ordering = ['id']


# ─────────────────────────────────────────────────────────────────────────────
# Événements et attributs — alimentent la frise chronologique
# ─────────────────────────────────────────────────────────────────────────────

class Event(models.Model):
    """
    Événement (BIRT, DEAT, MARR…) ou attribut (OCCU, RESI…), rattaché soit à un
    individu, soit à une famille.

    La date est conservée sous sa forme GEDCOM brute (date_raw, ex. « ABT 1875 »,
    « BET 1830 AND 1835 ») **et** sous forme analysée (date_start / date_end),
    pour trier et positionner l'événement sur la frise sans réinterpréter le
    texte à chaque affichage.
    """

    tree = models.ForeignKey(Tree, on_delete=models.CASCADE, related_name='events')
    individual = models.ForeignKey(
        Individual, on_delete=models.CASCADE, related_name='events', null=True, blank=True,
    )
    family = models.ForeignKey(
        Family, on_delete=models.CASCADE, related_name='events', null=True, blank=True,
    )

    tag = models.CharField(max_length=10, choices=EventTag.choices)
    #: TYPE — libellé libre, obligatoire pour EVEN/FACT
    custom_type = models.CharField(max_length=120, blank=True)
    #: Valeur d'un attribut (profession, résidence…)
    value = models.TextField(blank=True)

    # ── Date (GEDCOM DATE) ────────────────────────────────────────────────
    date_raw = models.CharField(max_length=120, blank=True)
    date_modifier = models.CharField(
        max_length=8, choices=DateModifier.choices, default=DateModifier.UNKNOWN, blank=True,
    )
    date_start = models.DateField(null=True, blank=True)
    date_end = models.DateField(null=True, blank=True)
    date_precision = models.CharField(
        max_length=6, choices=DatePrecision.choices, default=DatePrecision.NONE, blank=True,
    )
    calendar = models.CharField(max_length=10, choices=Calendar.choices, default=Calendar.GREGORIAN)
    date_phrase = models.CharField(max_length=250, blank=True)

    # ── Contexte ──────────────────────────────────────────────────────────
    place = models.ForeignKey(
        Place, on_delete=models.SET_NULL, related_name='events', null=True, blank=True,
    )
    address = models.TextField(blank=True)
    age = models.CharField(max_length=30, blank=True)      # AGE : « 72y 3m »
    agency = models.CharField(max_length=150, blank=True)  # AGNC
    cause = models.CharField(max_length=250, blank=True)   # CAUS
    religion = models.CharField(max_length=100, blank=True)
    note = models.TextField(blank=True)

    # ── Frise chronologique ───────────────────────────────────────────────
    category = models.CharField(max_length=12, choices=EventCategory.choices, default=EventCategory.OTHER)
    #: Vrai pour un intervalle (FROM…TO) : affiché comme une période, pas un point.
    is_period = models.BooleanField(default=False)
    color = models.CharField(max_length=20, blank=True)
    icon = models.CharField(max_length=40, blank=True)
    sort_order = models.PositiveSmallIntegerField(default=0)
    custom = models.JSONField(default=dict, blank=True)

    class Meta:
        db_table = 'events'
        ordering = ['date_start', 'sort_order', 'id']
        indexes = [
            models.Index(fields=['individual', 'tag']),
            models.Index(fields=['family', 'tag']),
            models.Index(fields=['tree', 'date_start']),
        ]
        constraints = [
            models.CheckConstraint(
                # Un événement appartient à un individu OU à une famille, jamais aux deux.
                condition=(
                    models.Q(individual__isnull=False, family__isnull=True)
                    | models.Q(individual__isnull=True, family__isnull=False)
                ),
                name='event_belongs_to_indi_xor_fam',
            ),
        ]

    def __str__(self) -> str:
        return f'{self.tag} {self.date_raw}'.strip()

    @property
    def is_attribute(self) -> bool:
        return self.tag in ATTRIBUTE_TAGS


# ─────────────────────────────────────────────────────────────────────────────
# Sources, dépôts, citations (SOUR / REPO)
# ─────────────────────────────────────────────────────────────────────────────

class Repository(models.Model):
    tree = models.ForeignKey(Tree, on_delete=models.CASCADE, related_name='repositories')
    xref_id = models.CharField(max_length=30, blank=True)
    name = models.CharField(max_length=250)
    address = models.TextField(blank=True)
    phone = models.CharField(max_length=50, blank=True)
    email = models.EmailField(blank=True)
    www = models.URLField(blank=True)

    class Meta:
        db_table = 'repositories'
        ordering = ['name']

    def __str__(self) -> str:
        return self.name


class Source(models.Model):
    tree = models.ForeignKey(Tree, on_delete=models.CASCADE, related_name='sources')
    xref_id = models.CharField(max_length=30, blank=True)
    title = models.CharField(max_length=300)
    author = models.CharField(max_length=250, blank=True)
    publication = models.CharField(max_length=300, blank=True)
    abbreviation = models.CharField(max_length=100, blank=True)
    text = models.TextField(blank=True)
    repository = models.ForeignKey(
        Repository, on_delete=models.SET_NULL, related_name='sources', null=True, blank=True,
    )
    call_number = models.CharField(max_length=100, blank=True)
    #: Renseigné quand la source vient d'un fournisseur (wikitree, familysearch…)
    provider = models.CharField(max_length=40, blank=True)
    external_url = models.URLField(max_length=500, blank=True)

    class Meta:
        db_table = 'sources'
        ordering = ['title']

    def __str__(self) -> str:
        return self.title


class Citation(models.Model):
    """SOUR pointant vers un individu, une famille ou un événement précis."""

    source = models.ForeignKey(Source, on_delete=models.CASCADE, related_name='citations')
    individual = models.ForeignKey(
        Individual, on_delete=models.CASCADE, related_name='citations', null=True, blank=True,
    )
    family = models.ForeignKey(
        Family, on_delete=models.CASCADE, related_name='citations', null=True, blank=True,
    )
    event = models.ForeignKey(
        Event, on_delete=models.CASCADE, related_name='citations', null=True, blank=True,
    )
    page = models.CharField(max_length=250, blank=True)
    #: QUAY 0–3 : 0 = non fiable, 3 = preuve directe
    quality = models.PositiveSmallIntegerField(null=True, blank=True)
    date_raw = models.CharField(max_length=120, blank=True)
    text = models.TextField(blank=True)

    class Meta:
        db_table = 'citations'
        ordering = ['id']


# ─────────────────────────────────────────────────────────────────────────────
# Médias (OBJE) — les images vivent dans la base
# ─────────────────────────────────────────────────────────────────────────────

class MediaObject(models.Model):
    tree = models.ForeignKey(Tree, on_delete=models.CASCADE, related_name='media')
    xref_id = models.CharField(max_length=30, blank=True)

    title = models.CharField(max_length=250, blank=True)
    description = models.TextField(blank=True)

    #: Contenu binaire du fichier (photo, scan d'acte…). NULL si external_url.
    data = models.BinaryField(null=True, blank=True, editable=True)
    mime = models.CharField(max_length=100, default='image/jpeg')
    filename = models.CharField(max_length=250, blank=True)
    size = models.PositiveIntegerField(default=0)
    checksum = models.CharField(max_length=64, blank=True, db_index=True)  # sha256
    width = models.PositiveSmallIntegerField(null=True, blank=True)
    height = models.PositiveSmallIntegerField(null=True, blank=True)

    #: Média resté chez un tiers (Wikimedia Commons, FamilySearch…)
    external_url = models.URLField(max_length=800, blank=True)
    provider = models.CharField(max_length=40, blank=True)
    attribution = models.CharField(max_length=300, blank=True)
    license = models.CharField(max_length=100, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'media_objects'
        ordering = ['-created_at']

    def __str__(self) -> str:
        return self.title or self.filename or f'Média #{self.pk}'


class MediaLink(models.Model):
    """
    Rattache un média à un individu / une famille / un événement.

    is_primary désigne le portrait affiché sur les cartes d'identité. Le cadrage
    (CROP de GEDCOM 7) permet d'extraire un visage d'une photo de groupe sans
    dupliquer le fichier.
    """

    media = models.ForeignKey(MediaObject, on_delete=models.CASCADE, related_name='links')
    individual = models.ForeignKey(
        Individual, on_delete=models.CASCADE, related_name='media_links', null=True, blank=True,
    )
    family = models.ForeignKey(
        Family, on_delete=models.CASCADE, related_name='media_links', null=True, blank=True,
    )
    event = models.ForeignKey(
        Event, on_delete=models.CASCADE, related_name='media_links', null=True, blank=True,
    )
    is_primary = models.BooleanField(default=False)

    # CROP — exprimé en pourcentage de l'image (0–100), indépendant de la résolution
    crop_x = models.FloatField(null=True, blank=True)
    crop_y = models.FloatField(null=True, blank=True)
    crop_width = models.FloatField(null=True, blank=True)
    crop_height = models.FloatField(null=True, blank=True)

    order = models.PositiveSmallIntegerField(default=0)

    class Meta:
        db_table = 'media_links'
        ordering = ['-is_primary', 'order', 'id']


class SharedNote(models.Model):
    """SNOTE — note réutilisable, citée par plusieurs enregistrements."""

    tree = models.ForeignKey(Tree, on_delete=models.CASCADE, related_name='shared_notes')
    xref_id = models.CharField(max_length=30, blank=True)
    text = models.TextField()

    class Meta:
        db_table = 'shared_notes'
        ordering = ['id']


# ─────────────────────────────────────────────────────────────────────────────
# Styles, positions et gabarits — la couche « édition visuelle »
# ─────────────────────────────────────────────────────────────────────────────

class NodeStyle(models.Model):
    """Apparence d'une carte dans l'arbre (réutilisable et nommée)."""

    tree = models.ForeignKey(Tree, on_delete=models.CASCADE, related_name='node_styles')
    name = models.CharField(max_length=80)
    is_default = models.BooleanField(default=False)

    background_color = models.CharField(max_length=30, default='#ffffff')
    background_gradient = models.CharField(max_length=120, blank=True)
    border_color = models.CharField(max_length=30, default='#d5dae2')
    border_width = models.PositiveSmallIntegerField(default=1)
    border_radius = models.PositiveSmallIntegerField(default=12)
    border_style = models.CharField(max_length=10, default='solid')  # solid/dashed/dotted

    text_color = models.CharField(max_length=30, default='#1f2733')
    accent_color = models.CharField(max_length=30, default='#1976d2')
    font_family = models.CharField(max_length=120, blank=True)
    font_size = models.PositiveSmallIntegerField(default=13)
    font_weight = models.CharField(max_length=10, default='500')

    shadow = models.CharField(max_length=10, default='sm')            # none/sm/md/lg
    photo_shape = models.CharField(max_length=10, default='rounded')  # circle/rounded/square
    photo_size = models.PositiveSmallIntegerField(default=56)

    width = models.PositiveSmallIntegerField(default=220)
    height = models.PositiveSmallIntegerField(default=86)
    opacity = models.FloatField(default=1.0)

    class Meta:
        db_table = 'node_styles'
        ordering = ['name']

    def __str__(self) -> str:
        return self.name


class StyleRule(models.Model):
    """
    Style conditionnel : « si sexe = F alors bordure rose », « si décédé alors gris ».

    condition suit la forme :
        {"all": [{"field": "sex", "op": "eq", "value": "F"}]}
        {"any": [...]}   — « all » et « any » sont combinables.

    Champs reconnus : sex, is_living, generation, surname, birth_year, death_year,
    has_photo, occupation, custom.<clé>.
    Opérateurs : eq, ne, lt, lte, gt, gte, in, contains, exists.
    """

    tree = models.ForeignKey(Tree, on_delete=models.CASCADE, related_name='style_rules')
    name = models.CharField(max_length=80)
    style = models.ForeignKey(NodeStyle, on_delete=models.CASCADE, related_name='rules')
    condition = models.JSONField(default=dict)
    priority = models.PositiveSmallIntegerField(default=0)
    enabled = models.BooleanField(default=True)

    class Meta:
        db_table = 'style_rules'
        ordering = ['-priority', 'id']

    def __str__(self) -> str:
        return self.name


class EdgeStyle(models.Model):
    """Apparence des liens qui relient les cartes."""

    class AppliesTo(models.TextChoices):
        PARENT_CHILD = 'PARENT_CHILD', 'Filiation'
        SPOUSE = 'SPOUSE', 'Union'
        ADOPTED = 'ADOPTED', 'Filiation adoptive'
        ASSOCIATION = 'ASSOCIATION', 'Association'

    tree = models.ForeignKey(Tree, on_delete=models.CASCADE, related_name='edge_styles')
    name = models.CharField(max_length=80)
    applies_to = models.CharField(max_length=15, choices=AppliesTo.choices, default=AppliesTo.PARENT_CHILD)
    is_default = models.BooleanField(default=False)

    color = models.CharField(max_length=30, default='#b6c0cd')
    width = models.PositiveSmallIntegerField(default=2)
    dash = models.CharField(max_length=10, default='solid')         # solid/dashed/dotted
    curve = models.CharField(max_length=12, default='orthogonal')   # orthogonal/bezier/straight
    marker_end = models.CharField(max_length=10, blank=True)        # none/arrow/dot
    opacity = models.FloatField(default=1.0)

    class Meta:
        db_table = 'edge_styles'
        ordering = ['name']

    def __str__(self) -> str:
        return self.name


class NodeLayout(models.Model):
    """
    Position d'un individu sur le canevas.

    pinned = l'utilisateur a déplacé la carte à la main : le moteur de mise en
    page automatique doit alors respecter cette position et ne pas la recalculer.
    """

    individual = models.OneToOneField(Individual, on_delete=models.CASCADE, related_name='layout')
    x = models.FloatField(default=0)
    y = models.FloatField(default=0)
    pinned = models.BooleanField(default=False)
    collapsed = models.BooleanField(default=False)
    hidden = models.BooleanField(default=False)
    generation = models.SmallIntegerField(default=0)
    z_index = models.SmallIntegerField(default=0)
    style_override = models.ForeignKey(
        NodeStyle, on_delete=models.SET_NULL, related_name='pinned_nodes', null=True, blank=True,
    )

    class Meta:
        db_table = 'node_layouts'


class FamilyLayout(models.Model):
    """Position du nœud de jonction d'une famille — le point où se rejoignent les liens."""

    family = models.OneToOneField(Family, on_delete=models.CASCADE, related_name='layout')
    x = models.FloatField(default=0)
    y = models.FloatField(default=0)
    pinned = models.BooleanField(default=False)

    class Meta:
        db_table = 'family_layouts'


class CardTemplate(models.Model):
    """
    Gabarit d'une carte d'identité — l'objet piloté par la page « Paramétrage ».

    MINI : photo à gauche, informations empilées à droite (nom, prénom, naissance,
           décès si décédé). C'est le nœud affiché dans l'arbre.
    FULL : photo en haut à gauche, mêmes informations à sa droite, et en dessous
           la frise chronologique de la vie. Affichée au clic sur une mini-carte.

    fields est une liste ordonnée de blocs :
        [{"key": "surname", "label": "Nom", "show": true, "order": 1,
          "bold": true, "size": 15, "color": "#1f2733", "uppercase": false}]
    Les clés admises sont listées dans AVAILABLE_FIELDS.
    """

    class Kind(models.TextChoices):
        MINI = 'MINI', 'Mini-carte'
        FULL = 'FULL', 'Grande carte'

    #: Champs proposés dans l'éditeur de gabarit.
    AVAILABLE_FIELDS = [
        'given_name', 'surname', 'full_name', 'nickname', 'birth_date', 'birth_place',
        'death_date', 'death_place', 'age', 'occupation', 'residence', 'sex',
        'lifespan', 'burial_date', 'marriage_date', 'note',
    ]

    tree = models.ForeignKey(Tree, on_delete=models.CASCADE, related_name='card_templates')
    kind = models.CharField(max_length=4, choices=Kind.choices)
    name = models.CharField(max_length=80)
    is_default = models.BooleanField(default=False)

    photo_position = models.CharField(max_length=10, default='LEFT')  # LEFT/TOP_LEFT/TOP/RIGHT/NONE
    photo_size = models.PositiveSmallIntegerField(default=56)
    photo_shape = models.CharField(max_length=10, default='rounded')
    #: Affiché quand l'individu n'a pas de photo : initiales ou silhouette
    photo_placeholder = models.CharField(max_length=12, default='initials')

    fields = models.JSONField(default=list)
    date_format = models.CharField(max_length=20, default='dd/MM/yyyy')
    #: Marque visuellement les personnes décédées (cross, sepia, band, none)
    deceased_marker = models.CharField(max_length=12, default='cross')

    # ── Spécifique à la grande carte ──────────────────────────────────────
    show_timeline = models.BooleanField(default=True)
    #: Catégories d'événements affichées sur la frise (vide = toutes)
    timeline_categories = models.JSONField(default=list, blank=True)
    timeline_orientation = models.CharField(max_length=12, default='horizontal')
    show_periods = models.BooleanField(default=True)
    show_sources = models.BooleanField(default=True)
    show_gallery = models.BooleanField(default=True)

    background_color = models.CharField(max_length=30, default='#ffffff')
    custom_css = models.TextField(blank=True)

    class Meta:
        db_table = 'card_templates'
        ordering = ['kind', 'name']

    def __str__(self) -> str:
        return f'{self.name} ({self.kind})'


class TreeViewSettings(models.Model):
    """Réglages de la vue arbre : placement, espacement, styles et gabarits par défaut."""

    class Layout(models.TextChoices):
        TIDY = 'TIDY', 'Arbre équilibré (Reingold-Tilford)'
        DAGRE = 'DAGRE', 'Hiérarchique (dagre)'
        RADIAL = 'RADIAL', 'Radial / éventail'
        HOURGLASS = 'HOURGLASS', 'Sablier (ascendants + descendants)'
        MANUAL = 'MANUAL', 'Libre (positions manuelles)'

    tree = models.OneToOneField(Tree, on_delete=models.CASCADE, related_name='view_settings')

    layout_algorithm = models.CharField(max_length=10, choices=Layout.choices, default=Layout.TIDY)
    orientation = models.CharField(max_length=2, default='TB')  # TB/BT/LR/RL
    node_spacing_x = models.PositiveSmallIntegerField(default=40)
    node_spacing_y = models.PositiveSmallIntegerField(default=90)
    generation_spacing = models.PositiveSmallIntegerField(default=120)

    zoom = models.FloatField(default=1.0)
    pan_x = models.FloatField(default=0)
    pan_y = models.FloatField(default=0)

    background_color = models.CharField(max_length=30, default='#f5f6fa')
    show_grid = models.BooleanField(default=False)
    snap_to_grid = models.BooleanField(default=False)
    grid_size = models.PositiveSmallIntegerField(default=16)

    show_spouses = models.BooleanField(default=True)
    show_photos = models.BooleanField(default=True)
    show_dates = models.BooleanField(default=True)
    generations_up = models.PositiveSmallIntegerField(default=4)
    generations_down = models.PositiveSmallIntegerField(default=4)

    root_individual = models.ForeignKey(
        Individual, on_delete=models.SET_NULL, related_name='+', null=True, blank=True,
    )
    default_mini_template = models.ForeignKey(
        CardTemplate, on_delete=models.SET_NULL, related_name='+', null=True, blank=True,
    )
    default_full_template = models.ForeignKey(
        CardTemplate, on_delete=models.SET_NULL, related_name='+', null=True, blank=True,
    )
    default_node_style = models.ForeignKey(
        NodeStyle, on_delete=models.SET_NULL, related_name='+', null=True, blank=True,
    )
    default_edge_style = models.ForeignKey(
        EdgeStyle, on_delete=models.SET_NULL, related_name='+', null=True, blank=True,
    )

    class Meta:
        db_table = 'tree_view_settings'


class CustomFieldDef(models.Model):
    """
    Champ défini par l'utilisateur, au-delà de GEDCOM.
    Les valeurs sont stockées dans Individual.custom / Family.custom / Event.custom.
    """

    class AppliesTo(models.TextChoices):
        INDIVIDUAL = 'INDIVIDUAL', 'Individu'
        FAMILY = 'FAMILY', 'Famille'
        EVENT = 'EVENT', 'Événement'

    class FieldType(models.TextChoices):
        TEXT = 'TEXT', 'Texte'
        LONGTEXT = 'LONGTEXT', 'Texte long'
        NUMBER = 'NUMBER', 'Nombre'
        DATE = 'DATE', 'Date'
        BOOL = 'BOOL', 'Oui / Non'
        URL = 'URL', 'Lien'
        SELECT = 'SELECT', 'Liste de choix'

    tree = models.ForeignKey(Tree, on_delete=models.CASCADE, related_name='custom_fields')
    applies_to = models.CharField(max_length=10, choices=AppliesTo.choices, default=AppliesTo.INDIVIDUAL)
    key = models.SlugField(max_length=40)
    label = models.CharField(max_length=80)
    field_type = models.CharField(max_length=8, choices=FieldType.choices, default=FieldType.TEXT)
    options = models.JSONField(default=list, blank=True)
    order = models.PositiveSmallIntegerField(default=0)

    class Meta:
        db_table = 'custom_field_defs'
        ordering = ['order', 'id']
        constraints = [
            models.UniqueConstraint(fields=['tree', 'applies_to', 'key'], name='uniq_custom_field_key'),
        ]


# ─────────────────────────────────────────────────────────────────────────────
# Enrichissement — résultats venus des fournisseurs externes
# ─────────────────────────────────────────────────────────────────────────────

class EnrichmentMatch(models.Model):
    """
    Candidat retourné par un fournisseur (WikiTree, Wikidata, FamilySearch…),
    conservé pour que l'utilisateur l'accepte ou le rejette depuis la page
    « Recherche & enrichissement ».
    """

    class Status(models.TextChoices):
        PENDING = 'PENDING', 'À examiner'
        ACCEPTED = 'ACCEPTED', 'Accepté'
        REJECTED = 'REJECTED', 'Rejeté'
        IMPORTED = 'IMPORTED', 'Importé'

    tree = models.ForeignKey(Tree, on_delete=models.CASCADE, related_name='enrichment_matches')
    individual = models.ForeignKey(
        Individual, on_delete=models.CASCADE, related_name='enrichment_matches', null=True, blank=True,
    )
    provider = models.CharField(max_length=40)
    external_id = models.CharField(max_length=200)
    external_url = models.URLField(max_length=800, blank=True)
    #: Réponse normalisée du fournisseur (voir providers.base.PersonResult)
    payload = models.JSONField(default=dict)
    score = models.FloatField(default=0)
    status = models.CharField(max_length=8, choices=Status.choices, default=Status.PENDING)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'enrichment_matches'
        ordering = ['-score', '-created_at']
        constraints = [
            models.UniqueConstraint(
                fields=['tree', 'provider', 'external_id'], name='uniq_match_per_tree_provider',
            ),
        ]


class ImportJob(models.Model):
    """Trace d'un import GEDCOM (ou d'un import depuis un fournisseur)."""

    class Status(models.TextChoices):
        RUNNING = 'RUNNING', 'En cours'
        DONE = 'DONE', 'Terminé'
        FAILED = 'FAILED', 'Échec'

    tree = models.ForeignKey(Tree, on_delete=models.CASCADE, related_name='import_jobs')
    filename = models.CharField(max_length=250, blank=True)
    source = models.CharField(max_length=40, default='gedcom')
    status = models.CharField(max_length=8, choices=Status.choices, default=Status.RUNNING)
    counts = models.JSONField(default=dict, blank=True)
    log = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'import_jobs'
        ordering = ['-created_at']
