/** Types partagés avec l'API Django. Alignés sur GEDCOM 7 côté données. */

export type Sex = 'M' | 'F' | 'X' | 'U';

export interface Tree {
  id: number;
  name: string;
  description: string;
  owner_email: string;
  is_public: boolean;
  individual_count: number;
  family_count: number;
  created_at: string;
  updated_at: string;
}

/** Style résolu d'une carte : le serveur a déjà appliqué les règles conditionnelles. */
export interface NodeStyle {
  background_color: string;
  background_gradient: string;
  border_color: string;
  border_width: number;
  border_radius: number;
  border_style: string;
  text_color: string;
  accent_color: string;
  font_family: string;
  font_size: number;
  font_weight: string;
  shadow: string;
  photo_shape: string;
  photo_size: number;
  width: number;
  height: number;
  opacity: number;
}

/** Un individu aplati, prêt à être affiché par une carte d'identité. */
export interface PersonCard {
  id: number;
  xref_id: string;
  sex: Sex;
  is_living: boolean;
  confidential: boolean;
  given_name: string;
  surname: string;
  nickname: string;
  full_name: string;
  birth_date: string;
  birth_place: string;
  birth_year: number | null;
  death_date: string;
  death_place: string;
  death_year: number | null;
  lifespan: string;
  occupation: string;
  residence: string;
  note: string;
  custom: Record<string, unknown>;
  photo_url: string | null;
  photo_crop: { x: number; y: number; width: number; height: number } | null;
  has_photo: boolean;
  generation: number;
  x: number;
  y: number;
  pinned: boolean;
  collapsed: boolean;
  hidden: boolean;
  style: NodeStyle;
}

/** Nœud de jonction d'une famille : les conjoints s'y relient, les enfants en partent. */
export interface FamilyNode {
  id: number;
  union_type: string;
  marriage_date: string;
  spouses: number[];
  children: number[];
  x: number;
  y: number;
  pinned: boolean;
  generation: number;
}

export interface Edge {
  source: string; // « i12 » (individu) ou « f3 » (famille)
  target: string;
  kind: 'PARENT_CHILD' | 'SPOUSE' | 'ADOPTED' | 'ASSOCIATION';
  role?: string;
  pedigree?: string;
}

export interface EdgeStyleSpec {
  color: string;
  width: number;
  dash: string;
  curve: string;
  marker_end: string;
  opacity: number;
}

/** Un bloc d'information dans une carte : quel champ, comment le styler. */
export interface CardField {
  key: string;
  label: string;
  show: boolean;
  order: number;
  bold?: boolean;
  size?: number;
  color?: string;
  uppercase?: boolean;
  prefix?: string;
  /** Masque le bloc tant que la personne est vivante (typiquement la date de décès). */
  hide_if_living?: boolean;
}

export interface CardTemplate {
  id: number;
  tree: number;
  kind: 'MINI' | 'FULL';
  name: string;
  is_default: boolean;
  photo_position: 'LEFT' | 'TOP_LEFT' | 'TOP' | 'RIGHT' | 'NONE';
  photo_size: number;
  photo_shape: string;
  photo_placeholder: string;
  fields: CardField[];
  date_format: string;
  deceased_marker: string;
  show_timeline: boolean;
  timeline_categories: string[];
  timeline_orientation: string;
  show_periods: boolean;
  show_sources: boolean;
  show_gallery: boolean;
  background_color: string;
  custom_css: string;
  available_fields?: string[];
}

export interface TreeViewSettings {
  id: number;
  tree: number;
  layout_algorithm: 'TIDY' | 'DAGRE' | 'RADIAL' | 'HOURGLASS' | 'MANUAL';
  orientation: 'TB' | 'BT' | 'LR' | 'RL';
  node_spacing_x: number;
  node_spacing_y: number;
  generation_spacing: number;
  zoom: number;
  pan_x: number;
  pan_y: number;
  background_color: string;
  show_grid: boolean;
  snap_to_grid: boolean;
  grid_size: number;
  show_spouses: boolean;
  show_photos: boolean;
  show_dates: boolean;
  root_individual: number | null;
}

export interface TreeGraph {
  tree: { id: number; name: string };
  settings: TreeViewSettings;
  nodes: PersonCard[];
  families: FamilyNode[];
  edges: Edge[];
  edge_styles: Record<string, EdgeStyleSpec>;
  mini_template: CardTemplate | null;
  full_template: CardTemplate | null;
}

/** Une entrée de la frise chronologique — cliquable pour en voir le détail. */
export interface TimelineEntry {
  id: number;
  scope: 'INDIVIDUAL' | 'FAMILY' | 'CHILD';
  tag: string;
  label: string;
  value: string;
  category: string;
  is_period: boolean;
  date_raw: string;
  date_phrase: string;
  start: string | null;
  end: string | null;
  start_year: number | null;
  end_year: number | null;
  place: string;
  latitude: number | null;
  longitude: number | null;
  age: string;
  cause: string;
  agency: string;
  note: string;
  color: string;
  icon: string;
  sort_order: number;
  related_individual?: number;
}

export interface Timeline {
  individual: number;
  entries: TimelineEntry[];
  span: { from: number; to: number } | null;
}

/** Description d'un fournisseur externe, telle que l'API la publie. */
export interface Provider {
  key: string;
  label: string;
  homepage: string;
  docs_url: string;
  requires_key: boolean;
  required_credentials: string[];
  optional_credentials?: string[];
  credential_help: string;
  supports_search: boolean;
  supports_fetch: boolean;
  supports_relatives: boolean;
  supports_geocoding?: boolean;
  coverage: string;
}

export interface ProviderRelative {
  relation: 'FATHER' | 'MOTHER' | 'SPOUSE' | 'CHILD' | 'SIBLING';
  external_id: string;
  name: string;
  sex: Sex;
  birth_date: string;
  death_date: string;
}

export interface ProviderResult {
  provider: string;
  external_id: string;
  url: string;
  given_name: string;
  surname: string;
  sex: Sex;
  birth_date: string;
  birth_place: string;
  death_date: string;
  death_place: string;
  occupation: string;
  description: string;
  photo_url: string;
  score: number;
  relatives: ProviderRelative[];
  raw: Record<string, unknown>;
}

export interface SearchPayload {
  provider: string;
  tree?: number;
  query: {
    given_name?: string;
    surname?: string;
    birth_year?: number | null;
    death_year?: number | null;
    place?: string;
    text?: string;
    limit?: number;
  };
  credentials?: Record<string, string>;
  save?: boolean;
}

export interface EventType {
  tag: string;
  label: string;
  category: string;
  scope: 'INDIVIDUAL' | 'FAMILY';
  is_attribute: boolean;
}

/** Un événement tel que l'API le stocke — c'est l'objet que l'éditeur manipule. */
export interface EventRecord {
  id: number;
  tree: number;
  individual: number | null;
  family: number | null;
  tag: string;
  custom_type: string;
  value: string;
  /** Seule date saisie : le serveur en déduit date_start/date_end/is_period. */
  date_raw: string;
  date_modifier: string;
  date_start: string | null;
  date_end: string | null;
  date_phrase: string;
  place: number | null;
  /** Le lieu se saisit par son nom ; il est créé côté serveur s'il n'existe pas. */
  place_name: string;
  address: string;
  age: string;
  agency: string;
  cause: string;
  religion: string;
  note: string;
  category: string;
  is_period: boolean;
  color: string;
  icon: string;
  sort_order: number;
  label: string;
  is_attribute: boolean;
}

export interface PersonalName {
  id: number;
  type: string;
  npfx: string;
  givn: string;
  nick: string;
  spfx: string;
  surn: string;
  nsfx: string;
  is_primary: boolean;
}

/** Fiche complète d'un individu (GET /api/individuals/<id>/). */
export interface IndividualDetail {
  id: number;
  tree: number;
  xref_id: string;
  sex: Sex;
  is_living: boolean;
  confidential: boolean;
  note: string;
  custom: Record<string, unknown>;
  names: PersonalName[];
  events: EventRecord[];
  given_name: string;
  surname: string;
  nickname: string;
  full_name: string;
  birth_date: string;
  death_date: string;
  photo_url: string | null;
  /** Familles où la personne est conjoint : leurs mariages/divorces sont éditables ici. */
  spouse_families: number[];
}

export const CATEGORY_COLORS: Record<string, string> = {
  LIFE: '#1976d2',
  FAMILY: '#c2185b',
  EDUCATION: '#7b1fa2',
  WORK: '#00796b',
  MILITARY: '#5d4037',
  RESIDENCE: '#f57c00',
  RELIGION: '#455a64',
  HEALTH: '#d32f2f',
  LEGAL: '#616161',
  MIGRATION: '#0288d1',
  OTHER: '#9e9e9e',
};

export const CATEGORY_LABELS: Record<string, string> = {
  LIFE: 'Vie',
  FAMILY: 'Famille',
  EDUCATION: 'Éducation',
  WORK: 'Travail',
  MILITARY: 'Militaire',
  RESIDENCE: 'Résidence',
  RELIGION: 'Religion',
  HEALTH: 'Santé',
  LEGAL: 'Juridique',
  MIGRATION: 'Migration',
  OTHER: 'Autre',
};
