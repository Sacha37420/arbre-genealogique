"""Sérialiseurs DRF. Les écritures de dates passent toutes par le parseur GEDCOM."""
import base64
import hashlib

from rest_framework import serializers

from .gedcom import category_for, parse_date
from .models import (
    CardTemplate,
    Citation,
    CustomFieldDef,
    EdgeStyle,
    EnrichmentMatch,
    Event,
    Family,
    FamilyChild,
    FamilyLayout,
    FamilySpouse,
    ImportJob,
    Individual,
    MediaLink,
    MediaObject,
    NodeLayout,
    NodeStyle,
    PersonalName,
    Place,
    Repository,
    SharedNote,
    Source,
    StyleRule,
    Tree,
    TreeViewSettings,
    UserRecord,
)


class UserRecordSerializer(serializers.ModelSerializer):
    class Meta:
        model = UserRecord
        fields = ['email', 'display_name', 'registered_at']


class TreeSerializer(serializers.ModelSerializer):
    individual_count = serializers.IntegerField(source='individuals.count', read_only=True)
    family_count = serializers.IntegerField(source='families.count', read_only=True)

    class Meta:
        model = Tree
        fields = [
            'id', 'name', 'description', 'owner_email', 'is_public', 'gedcom_version',
            'source_software', 'submitter_name', 'copyright', 'language', 'note',
            'individual_count', 'family_count', 'created_at', 'updated_at',
        ]
        read_only_fields = ['owner_email', 'created_at', 'updated_at']


class PlaceSerializer(serializers.ModelSerializer):
    class Meta:
        model = Place
        fields = [
            'id', 'tree', 'name', 'hierarchy', 'latitude', 'longitude',
            'geocode_provider', 'geocoded_at', 'note',
        ]
        read_only_fields = ['geocoded_at']


class PersonalNameSerializer(serializers.ModelSerializer):
    display = serializers.CharField(source='__str__', read_only=True)

    class Meta:
        model = PersonalName
        fields = [
            'id', 'individual', 'type', 'npfx', 'givn', 'nick', 'spfx', 'surn', 'nsfx',
            'is_primary', 'order', 'display',
        ]
        extra_kwargs = {'individual': {'required': False}}


class EventSerializer(serializers.ModelSerializer):
    place_name = serializers.CharField(source='place.name', read_only=True)
    label = serializers.SerializerMethodField()
    is_attribute = serializers.BooleanField(read_only=True)

    class Meta:
        model = Event
        fields = [
            'id', 'tree', 'individual', 'family', 'tag', 'custom_type', 'value',
            'date_raw', 'date_modifier', 'date_start', 'date_end', 'date_precision',
            'calendar', 'date_phrase', 'place', 'place_name', 'address', 'age', 'agency',
            'cause', 'religion', 'note', 'category', 'is_period', 'color', 'icon',
            'sort_order', 'custom', 'label', 'is_attribute',
        ]
        read_only_fields = [
            'date_modifier', 'date_start', 'date_end', 'date_precision', 'is_period',
        ]

    def get_label(self, obj: Event) -> str:
        return obj.custom_type or obj.get_tag_display()

    def validate(self, attrs):
        individual = attrs.get('individual', getattr(self.instance, 'individual', None))
        family = attrs.get('family', getattr(self.instance, 'family', None))
        if bool(individual) == bool(family):
            raise serializers.ValidationError(
                "Un événement doit être rattaché à un individu OU à une famille."
            )
        return attrs

    def _apply_date(self, validated: dict) -> dict:
        """La date brute est la seule saisie ; les champs analysés en découlent."""
        if 'date_raw' in validated:
            validated.update(parse_date(validated['date_raw']).as_fields())
        if 'tag' in validated and not validated.get('category'):
            validated['category'] = category_for(validated['tag'])
        return validated

    def create(self, validated_data):
        return super().create(self._apply_date(validated_data))

    def update(self, instance, validated_data):
        return super().update(instance, self._apply_date(validated_data))


class MediaObjectSerializer(serializers.ModelSerializer):
    """
    Le binaire n'est jamais renvoyé dans le JSON : il est servi par
    GET /api/media/<id>/file/. En écriture, on accepte du base64 (data_base64).
    """

    data_base64 = serializers.CharField(write_only=True, required=False, allow_blank=True)
    file_url = serializers.SerializerMethodField()

    class Meta:
        model = MediaObject
        fields = [
            'id', 'tree', 'xref_id', 'title', 'description', 'mime', 'filename', 'size',
            'checksum', 'width', 'height', 'external_url', 'provider', 'attribution',
            'license', 'created_at', 'data_base64', 'file_url',
        ]
        read_only_fields = ['size', 'checksum', 'created_at']

    def get_file_url(self, obj: MediaObject) -> str:
        if obj.data:
            return f'/api/media/{obj.pk}/file/'
        return obj.external_url

    def _decode(self, validated: dict) -> dict:
        raw = validated.pop('data_base64', None)
        if not raw:
            return validated
        # Accepte aussi bien « data:image/png;base64,iVBOR… » que le base64 nu.
        if ',' in raw and raw.strip().startswith('data:'):
            header, raw = raw.split(',', 1)
            if 'mime' not in validated and ':' in header and ';' in header:
                validated['mime'] = header.split(':', 1)[1].split(';', 1)[0]
        blob = base64.b64decode(raw)
        validated['data'] = blob
        validated['size'] = len(blob)
        validated['checksum'] = hashlib.sha256(blob).hexdigest()
        return validated

    def create(self, validated_data):
        return super().create(self._decode(validated_data))

    def update(self, instance, validated_data):
        return super().update(instance, self._decode(validated_data))


class MediaLinkSerializer(serializers.ModelSerializer):
    file_url = serializers.SerializerMethodField()
    title = serializers.CharField(source='media.title', read_only=True)

    class Meta:
        model = MediaLink
        fields = [
            'id', 'media', 'individual', 'family', 'event', 'is_primary',
            'crop_x', 'crop_y', 'crop_width', 'crop_height', 'order', 'file_url', 'title',
        ]

    def get_file_url(self, obj: MediaLink) -> str:
        return f'/api/media/{obj.media_id}/file/' if obj.media.data else obj.media.external_url


class IndividualSerializer(serializers.ModelSerializer):
    names = PersonalNameSerializer(many=True, read_only=True)
    events = EventSerializer(many=True, read_only=True)
    media_links = MediaLinkSerializer(many=True, read_only=True)

    # Champs de confort, calculés — ce sont eux qu'affichent les cartes d'identité.
    given_name = serializers.SerializerMethodField()
    surname = serializers.SerializerMethodField()
    full_name = serializers.SerializerMethodField()
    birth_date = serializers.SerializerMethodField()
    death_date = serializers.SerializerMethodField()
    photo_url = serializers.SerializerMethodField()

    class Meta:
        model = Individual
        fields = [
            'id', 'tree', 'xref_id', 'uid', 'sex', 'is_living', 'confidential', 'note',
            'custom', 'names', 'events', 'media_links', 'given_name', 'surname',
            'full_name', 'birth_date', 'death_date', 'photo_url',
            'created_at', 'updated_at',
        ]
        read_only_fields = ['uid', 'created_at', 'updated_at']

    def _primary_name(self, obj):
        return next((n for n in obj.names.all() if n.is_primary), None) or obj.names.first()

    def get_given_name(self, obj) -> str:
        name = self._primary_name(obj)
        return name.givn if name else ''

    def get_surname(self, obj) -> str:
        name = self._primary_name(obj)
        return ' '.join(p for p in (name.spfx, name.surn) if p) if name else ''

    def get_full_name(self, obj) -> str:
        name = self._primary_name(obj)
        return str(name) if name else ''

    def _event_date(self, obj, *tags) -> str:
        event = next((e for e in obj.events.all() if e.tag in tags), None)
        return event.date_raw if event else ''

    def get_birth_date(self, obj) -> str:
        return self._event_date(obj, 'BIRT', 'CHR', 'BAPM')

    def get_death_date(self, obj) -> str:
        return self._event_date(obj, 'DEAT', 'BURI', 'CREM')

    def get_photo_url(self, obj) -> str | None:
        link = next((l for l in obj.media_links.all() if l.is_primary), None)
        if not link:
            return None
        return f'/api/media/{link.media_id}/file/' if link.media.data else link.media.external_url


class IndividualWriteSerializer(serializers.ModelSerializer):
    """
    Création/mise à jour d'un individu avec son nom principal en une requête —
    le cas courant depuis l'interface, où l'on saisit « Jean Dupont » d'un bloc.
    """

    givn = serializers.CharField(required=False, allow_blank=True, write_only=True)
    surn = serializers.CharField(required=False, allow_blank=True, write_only=True)

    class Meta:
        model = Individual
        fields = ['id', 'tree', 'xref_id', 'sex', 'is_living', 'confidential', 'note',
                  'custom', 'givn', 'surn']

    def create(self, validated_data):
        givn = validated_data.pop('givn', '')
        surn = validated_data.pop('surn', '')
        individual = super().create(validated_data)
        if givn or surn:
            PersonalName.objects.create(individual=individual, givn=givn, surn=surn, is_primary=True)
        return individual

    def update(self, instance, validated_data):
        givn = validated_data.pop('givn', None)
        surn = validated_data.pop('surn', None)
        individual = super().update(instance, validated_data)
        if givn is not None or surn is not None:
            name = individual.names.filter(is_primary=True).first()
            if name is None:
                name = PersonalName(individual=individual, is_primary=True)
            if givn is not None:
                name.givn = givn
            if surn is not None:
                name.surn = surn
            name.save()
        return individual


class FamilySpouseSerializer(serializers.ModelSerializer):
    class Meta:
        model = FamilySpouse
        fields = ['id', 'family', 'individual', 'role', 'order']


class FamilyChildSerializer(serializers.ModelSerializer):
    class Meta:
        model = FamilyChild
        fields = ['id', 'family', 'individual', 'pedigree', 'status', 'order']


class FamilySerializer(serializers.ModelSerializer):
    spouses = FamilySpouseSerializer(many=True, read_only=True)
    children = FamilyChildSerializer(many=True, read_only=True)
    events = EventSerializer(many=True, read_only=True)

    class Meta:
        model = Family
        fields = [
            'id', 'tree', 'xref_id', 'union_type', 'note', 'custom',
            'spouses', 'children', 'events', 'created_at', 'updated_at',
        ]
        read_only_fields = ['created_at', 'updated_at']


class RepositorySerializer(serializers.ModelSerializer):
    class Meta:
        model = Repository
        fields = '__all__'


class SourceSerializer(serializers.ModelSerializer):
    class Meta:
        model = Source
        fields = '__all__'


class CitationSerializer(serializers.ModelSerializer):
    source_title = serializers.CharField(source='source.title', read_only=True)

    class Meta:
        model = Citation
        fields = [
            'id', 'source', 'source_title', 'individual', 'family', 'event',
            'page', 'quality', 'date_raw', 'text',
        ]


class SharedNoteSerializer(serializers.ModelSerializer):
    class Meta:
        model = SharedNote
        fields = '__all__'


# ── Styles, positions, gabarits ──────────────────────────────────────────────

class NodeStyleSerializer(serializers.ModelSerializer):
    class Meta:
        model = NodeStyle
        fields = '__all__'


class StyleRuleSerializer(serializers.ModelSerializer):
    class Meta:
        model = StyleRule
        fields = '__all__'


class EdgeStyleSerializer(serializers.ModelSerializer):
    class Meta:
        model = EdgeStyle
        fields = '__all__'


class NodeLayoutSerializer(serializers.ModelSerializer):
    class Meta:
        model = NodeLayout
        fields = '__all__'


class FamilyLayoutSerializer(serializers.ModelSerializer):
    class Meta:
        model = FamilyLayout
        fields = '__all__'


class CardTemplateSerializer(serializers.ModelSerializer):
    available_fields = serializers.SerializerMethodField()

    class Meta:
        model = CardTemplate
        fields = [
            'id', 'tree', 'kind', 'name', 'is_default', 'photo_position', 'photo_size',
            'photo_shape', 'photo_placeholder', 'fields', 'date_format', 'deceased_marker',
            'show_timeline', 'timeline_categories', 'timeline_orientation', 'show_periods',
            'show_sources', 'show_gallery', 'background_color', 'custom_css',
            'available_fields',
        ]

    def get_available_fields(self, obj) -> list[str]:
        return CardTemplate.AVAILABLE_FIELDS


class TreeViewSettingsSerializer(serializers.ModelSerializer):
    class Meta:
        model = TreeViewSettings
        fields = '__all__'


class CustomFieldDefSerializer(serializers.ModelSerializer):
    class Meta:
        model = CustomFieldDef
        fields = '__all__'


class EnrichmentMatchSerializer(serializers.ModelSerializer):
    class Meta:
        model = EnrichmentMatch
        fields = [
            'id', 'tree', 'individual', 'provider', 'external_id', 'external_url',
            'payload', 'score', 'status', 'created_at',
        ]
        read_only_fields = ['created_at']


class ImportJobSerializer(serializers.ModelSerializer):
    class Meta:
        model = ImportJob
        fields = '__all__'


# ── Positions envoyées en lot depuis le canevas ──────────────────────────────

class NodePositionSerializer(serializers.Serializer):
    """Une position déplacée à la souris, dans le lot envoyé par la page arbre."""

    individual = serializers.IntegerField(required=False)
    family = serializers.IntegerField(required=False)
    x = serializers.FloatField()
    y = serializers.FloatField()
    pinned = serializers.BooleanField(default=True)

    def validate(self, attrs):
        if bool(attrs.get('individual')) == bool(attrs.get('family')):
            raise serializers.ValidationError("Renseigner « individual » ou « family », pas les deux.")
        return attrs


class LayoutBulkSerializer(serializers.Serializer):
    positions = NodePositionSerializer(many=True)
