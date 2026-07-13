import { Component, computed, effect, inject, signal } from '@angular/core';
import { FormsModule } from '@angular/forms';

import { ApiService } from '../../core/api.service';
import {
  CATEGORY_LABELS,
  CardField,
  CardTemplate,
  PersonCard,
  Timeline,
  TreeGraph,
} from '../../core/models';
import { TreeStore, describeError } from '../../core/tree-store';
import { FullCardComponent } from '../../shared/cards/full-card.component';
import { MiniCardComponent } from '../../shared/cards/mini-card.component';

/** Libellés proposés pour les champs que l'API déclare disponibles. */
const FIELD_LABELS: Record<string, string> = {
  given_name: 'Prénom',
  surname: 'Nom',
  full_name: 'Nom complet',
  nickname: 'Surnom',
  birth_date: 'Date de naissance',
  birth_place: 'Lieu de naissance',
  death_date: 'Date de décès',
  death_place: 'Lieu de décès',
  age: 'Âge',
  occupation: 'Profession',
  residence: 'Résidence',
  sex: 'Sexe',
  lifespan: 'Années (1875 – 1940)',
  burial_date: 'Date d’inhumation',
  marriage_date: 'Date de mariage',
  note: 'Note',
};

/** Carte de démonstration : permet de régler les gabarits sur un arbre encore vide. */
const DEMO_CARD: PersonCard = {
  id: -1,
  xref_id: '@I0@',
  sex: 'M',
  is_living: false,
  confidential: false,
  given_name: 'Jean-Baptiste',
  surname: 'Dupont',
  nickname: 'Baptiste',
  full_name: 'Jean-Baptiste Dupont',
  birth_date: '12 JAN 1875',
  birth_place: 'Lyon, Rhône, France',
  birth_year: 1875,
  death_date: 'ABT 1940',
  death_place: 'Paris, France',
  death_year: 1940,
  lifespan: '1875 – 1940',
  occupation: 'Charpentier',
  residence: 'Lyon',
  note: '',
  custom: {},
  photo_url: null,
  photo_crop: null,
  has_photo: false,
  generation: 0,
  x: 0,
  y: 0,
  pinned: false,
  collapsed: false,
  hidden: false,
  style: {
    background_color: '#ffffff',
    background_gradient: '',
    border_color: '#d5dae2',
    border_width: 1,
    border_radius: 12,
    border_style: 'solid',
    text_color: '#1f2733',
    accent_color: '#1976d2',
    font_family: '',
    font_size: 13,
    font_weight: '500',
    shadow: 'sm',
    photo_shape: 'rounded',
    photo_size: 56,
    width: 220,
    height: 86,
    opacity: 1,
  },
};

const DEMO_TIMELINE: Timeline = {
  individual: -1,
  entries: [
    {
      id: -1, scope: 'INDIVIDUAL', tag: 'BIRT', label: 'Naissance', value: '', category: 'LIFE',
      is_period: false, date_raw: '12 JAN 1875', date_phrase: '', start: '1875-01-12',
      end: '1875-01-12', start_year: 1875, end_year: 1875, place: 'Lyon, Rhône, France',
      latitude: null, longitude: null, age: '', cause: '', agency: '', note: '', color: '',
      icon: '', sort_order: 0,
    },
    {
      id: -2, scope: 'INDIVIDUAL', tag: 'OCCU', label: 'Profession', value: 'Charpentier',
      category: 'WORK', is_period: true, date_raw: 'FROM 1893 TO 1935', date_phrase: '',
      start: '1893-01-01', end: '1935-12-31', start_year: 1893, end_year: 1935,
      place: 'Lyon', latitude: null, longitude: null, age: '', cause: '', agency: '',
      note: '', color: '', icon: '', sort_order: 0,
    },
    {
      id: -3, scope: 'FAMILY', tag: 'MARR', label: 'Mariage', value: '', category: 'FAMILY',
      is_period: false, date_raw: '3 JUN 1900', date_phrase: '', start: '1900-06-03',
      end: '1900-06-03', start_year: 1900, end_year: 1900, place: 'Lyon', latitude: null,
      longitude: null, age: '25 ans', cause: '', agency: '', note: '', color: '', icon: '',
      sort_order: 0,
    },
    {
      id: -4, scope: 'INDIVIDUAL', tag: 'DEAT', label: 'Décès', value: '', category: 'LIFE',
      is_period: false, date_raw: 'ABT 1940', date_phrase: '', start: '1940-01-01',
      end: '1940-12-31', start_year: 1940, end_year: 1940, place: 'Paris, France',
      latitude: null, longitude: null, age: '65 ans', cause: '', agency: '', note: '',
      color: '', icon: '', sort_order: 0,
    },
  ],
  span: { from: 1875, to: 1940 },
};

@Component({
  selector: 'app-settings',
  standalone: true,
  imports: [FormsModule, MiniCardComponent, FullCardComponent],
  templateUrl: './settings.component.html',
  styleUrl: './settings.component.scss',
})
export class SettingsComponent {
  private api = inject(ApiService);
  readonly store = inject(TreeStore);

  readonly tab = signal<'MINI' | 'FULL'>('MINI');
  readonly templates = signal<CardTemplate[]>([]);
  readonly graph = signal<TreeGraph | null>(null);
  readonly loading = signal(false);
  readonly saving = signal(false);
  readonly saved = signal(false);
  readonly error = signal('');

  readonly categoryOptions = Object.entries(CATEGORY_LABELS).map(([key, label]) => ({ key, label }));
  readonly photoPositions = [
    { value: 'LEFT', label: 'À gauche' },
    { value: 'TOP_LEFT', label: 'En haut à gauche' },
    { value: 'TOP', label: 'Au-dessus' },
    { value: 'RIGHT', label: 'À droite' },
    { value: 'NONE', label: 'Aucune photo' },
  ];

  constructor() {
    this.store.load();

    effect(() => {
      const treeId = this.store.currentId();
      if (treeId === null) {
        this.templates.set([]);
        this.graph.set(null);
        return;
      }
      this.load(treeId);
    });
  }

  private load(treeId: number): void {
    this.loading.set(true);
    this.api.getCardTemplates(treeId).subscribe({
      next: (templates) => {
        this.templates.set(templates);
        this.loading.set(false);
      },
      error: (err) => {
        this.error.set(describeError(err));
        this.loading.set(false);
      },
    });

    // L'aperçu se fait sur une personne réelle de l'arbre quand il y en a une.
    this.api.getGraph(treeId).subscribe({
      next: (graph) => {
        this.graph.set(graph);
        const first = graph.nodes[0];
        if (first) {
          this.api.getTimeline(first.id).subscribe({
            next: (timeline) => this.realTimeline.set(timeline),
            error: () => this.realTimeline.set(null),
          });
        }
      },
      error: () => this.graph.set(null),
    });
  }

  /** Gabarit en cours d'édition. */
  readonly current = computed(
    () => this.templates().find((t) => t.kind === this.tab()) ?? null,
  );

  /** Individu servant d'aperçu : le premier de l'arbre, sinon la carte de démonstration. */
  readonly previewCard = computed(() => this.graph()?.nodes[0] ?? DEMO_CARD);

  private readonly realTimeline = signal<Timeline | null>(null);

  /** Frise de l'aperçu : celle du vrai individu, ou une frise fictive si l'arbre est vide. */
  readonly previewTimeline = computed(() =>
    this.graph()?.nodes.length ? this.realTimeline() : DEMO_TIMELINE,
  );

  readonly availableToAdd = computed(() => {
    const template = this.current();
    if (!template) return [];
    const used = new Set((template.fields ?? []).map((f) => f.key));
    return (template.available_fields ?? Object.keys(FIELD_LABELS))
      .filter((key) => !used.has(key))
      .map((key) => ({ key, label: FIELD_LABELS[key] ?? key }));
  });

  labelFor(key: string): string {
    return FIELD_LABELS[key] ?? key;
  }

  // ── Édition des champs ────────────────────────────────────────────────────
  private patch(changes: Partial<CardTemplate>): void {
    const template = this.current();
    if (!template) return;
    this.templates.update((list) =>
      list.map((t) => (t.id === template.id ? { ...t, ...changes } : t)),
    );
    this.saved.set(false);
  }

  patchTemplate(key: keyof CardTemplate, value: unknown): void {
    this.patch({ [key]: value } as Partial<CardTemplate>);
  }

  patchField(index: number, changes: Partial<CardField>): void {
    const template = this.current();
    if (!template) return;
    const fields = template.fields.map((field, i) => (i === index ? { ...field, ...changes } : field));
    this.patch({ fields });
  }

  moveField(index: number, direction: -1 | 1): void {
    const template = this.current();
    if (!template) return;

    const target = index + direction;
    const fields = [...template.fields];
    if (target < 0 || target >= fields.length) return;

    [fields[index], fields[target]] = [fields[target], fields[index]];
    // L'ordre affiché est celui du tableau : on renumérote pour que le serveur le retienne.
    this.patch({ fields: fields.map((field, i) => ({ ...field, order: i + 1 })) });
  }

  removeField(index: number): void {
    const template = this.current();
    if (!template) return;
    const fields = template.fields
      .filter((_, i) => i !== index)
      .map((field, i) => ({ ...field, order: i + 1 }));
    this.patch({ fields });
  }

  addField(key: string): void {
    const template = this.current();
    if (!template || !key) return;

    const field: CardField = {
      key,
      label: FIELD_LABELS[key] ?? key,
      show: true,
      order: template.fields.length + 1,
      bold: false,
      size: template.kind === 'FULL' ? 14 : 12,
      color: '#42505f',
      uppercase: false,
    };
    this.patch({ fields: [...template.fields, field] });
  }

  toggleCategory(key: string, checked: boolean): void {
    const template = this.current();
    if (!template) return;

    const categories = new Set(template.timeline_categories ?? []);
    if (checked) {
      categories.add(key);
    } else {
      categories.delete(key);
    }
    this.patch({ timeline_categories: [...categories] });
  }

  isCategoryOn(key: string): boolean {
    const categories = this.current()?.timeline_categories ?? [];
    // Liste vide = aucune restriction : toutes les catégories s'affichent.
    return categories.length === 0 || categories.includes(key);
  }

  // ── Enregistrement ────────────────────────────────────────────────────────
  save(): void {
    const template = this.current();
    if (!template) return;

    this.saving.set(true);
    this.error.set('');

    this.api
      .updateCardTemplate(template.id, {
        fields: template.fields,
        photo_position: template.photo_position,
        photo_size: template.photo_size,
        photo_shape: template.photo_shape,
        photo_placeholder: template.photo_placeholder,
        date_format: template.date_format,
        deceased_marker: template.deceased_marker,
        show_timeline: template.show_timeline,
        timeline_categories: template.timeline_categories,
        show_periods: template.show_periods,
        show_sources: template.show_sources,
        show_gallery: template.show_gallery,
        background_color: template.background_color,
      })
      .subscribe({
        next: () => {
          this.saving.set(false);
          this.saved.set(true);
        },
        error: (err) => {
          this.saving.set(false);
          this.error.set(describeError(err));
        },
      });
  }
}
