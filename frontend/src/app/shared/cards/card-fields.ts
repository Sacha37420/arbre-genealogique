import { CardField, CardTemplate, PersonCard } from '../../core/models';

const MONTHS = ['JAN', 'FEB', 'MAR', 'APR', 'MAY', 'JUN', 'JUL', 'AUG', 'SEP', 'OCT', 'NOV', 'DEC'];

const QUALIFIERS: [RegExp, (rest: string) => string][] = [
  [/^ABT\s+(.+)$/i, (rest) => `vers ${rest}`],
  [/^CAL\s+(.+)$/i, (rest) => `calculé ${rest}`],
  [/^EST\s+(.+)$/i, (rest) => `estimé ${rest}`],
  [/^BEF\s+(.+)$/i, (rest) => `avant ${rest}`],
  [/^AFT\s+(.+)$/i, (rest) => `après ${rest}`],
];

/**
 * Rend lisible une date GEDCOM.
 *
 * Le format brut (« ABT 1875 », « BET 1830 AND 1835 ») porte une nuance que l'on
 * perdrait à l'afficher tel quel ou à le réduire à une année : on le traduit sans
 * jamais inventer une précision que la source ne donne pas.
 */
export function formatGedcomDate(raw: string, format = 'dd/MM/yyyy'): string {
  if (!raw) return '';
  const value = raw.trim();

  const between = /^BET\s+(.+?)\s+AND\s+(.+)$/i.exec(value);
  if (between) {
    return `entre ${formatGedcomDate(between[1], format)} et ${formatGedcomDate(between[2], format)}`;
  }

  const period = /^FROM\s+(.+?)\s+TO\s+(.+)$/i.exec(value);
  if (period) {
    return `de ${formatGedcomDate(period[1], format)} à ${formatGedcomDate(period[2], format)}`;
  }

  const from = /^FROM\s+(.+)$/i.exec(value);
  if (from) return `à partir de ${formatGedcomDate(from[1], format)}`;

  const to = /^TO\s+(.+)$/i.exec(value);
  if (to) return `jusqu’à ${formatGedcomDate(to[1], format)}`;

  for (const [pattern, render] of QUALIFIERS) {
    const match = pattern.exec(value);
    if (match) return render(formatGedcomDate(match[1], format));
  }

  return formatSimple(value, format);
}

function formatSimple(value: string, format: string): string {
  const match = /^(?:(\d{1,2})\s+)?(?:([A-Za-z]{3})\s+)?(\d{1,4})$/.exec(value);
  if (!match) return value;

  const [, day, monthTag, year] = match;
  const monthIndex = monthTag ? MONTHS.indexOf(monthTag.toUpperCase()) : -1;

  if (monthIndex < 0) return year;

  const month = String(monthIndex + 1).padStart(2, '0');
  if (!day) return format.includes('/') ? `${month}/${year}` : `${month} ${year}`;

  const paddedDay = day.padStart(2, '0');
  return format
    .replace('dd', paddedDay)
    .replace('MM', month)
    .replace('yyyy', year);
}

export interface ResolvedField {
  key: string;
  label: string;
  value: string;
  bold: boolean;
  size: number;
  color: string;
  uppercase: boolean;
}

/**
 * Applique un gabarit à un individu : ne restent que les blocs affichables.
 *
 * Un bloc vide disparaît (une carte ne doit pas montrer « Décès : » sans date), et
 * `hide_if_living` masque le décès des personnes vivantes.
 */
export function resolveFields(card: PersonCard, template: CardTemplate | null): ResolvedField[] {
  if (!template) return [];

  return [...(template.fields ?? [])]
    .filter((field) => field.show)
    .sort((a, b) => a.order - b.order)
    .map((field) => ({
      key: field.key,
      label: field.label,
      value: fieldValue(card, field, template.date_format),
      bold: field.bold ?? false,
      size: field.size ?? 13,
      color: field.color ?? '#1f2733',
      uppercase: field.uppercase ?? false,
    }))
    .filter((field) => field.value !== '');
}

function fieldValue(card: PersonCard, field: CardField, dateFormat: string): string {
  if (field.hide_if_living && card.is_living) return '';

  const prefix = field.prefix ?? '';
  const raw = rawValue(card, field.key, dateFormat);
  return raw ? `${prefix}${raw}` : '';
}

function rawValue(card: PersonCard, key: string, dateFormat: string): string {
  switch (key) {
    case 'given_name':
      return card.given_name;
    case 'surname':
      return card.surname;
    case 'full_name':
      return card.full_name;
    case 'nickname':
      return card.nickname;
    case 'birth_date':
      return formatGedcomDate(card.birth_date, dateFormat);
    case 'birth_place':
      return card.birth_place;
    case 'death_date':
      return formatGedcomDate(card.death_date, dateFormat);
    case 'death_place':
      return card.death_place;
    case 'lifespan':
      return card.lifespan;
    case 'occupation':
      return card.occupation;
    case 'residence':
      return card.residence;
    case 'sex':
      return { M: 'Homme', F: 'Femme', X: 'Autre', U: '' }[card.sex] ?? '';
    case 'age':
      return computeAge(card);
    case 'note':
      return card.note;
    default:
      return String(card.custom?.[key] ?? '');
  }
}

/** Âge au décès, ou âge actuel si la personne est vivante. */
function computeAge(card: PersonCard): string {
  if (!card.birth_year) return '';
  const end = card.death_year ?? (card.is_living ? new Date().getFullYear() : null);
  if (!end) return '';
  const age = end - card.birth_year;
  if (age < 0 || age > 130) return '';
  return card.is_living ? `${age} ans` : `${age} ans (au décès)`;
}

export function initials(card: PersonCard): string {
  const first = card.given_name.trim()[0] ?? '';
  const last = card.surname.trim()[0] ?? '';
  return `${first}${last}`.toUpperCase() || '?';
}

/** CSS du cadrage GEDCOM (CROP) : agrandit et décale l'image pour isoler un visage. */
export function cropStyle(card: PersonCard): Record<string, string> {
  const crop = card.photo_crop;
  if (!crop || !crop.width || !crop.height) {
    return { 'object-fit': 'cover' };
  }
  const scaleX = 100 / crop.width;
  const scaleY = 100 / crop.height;
  return {
    'object-fit': 'cover',
    transform: `scale(${Math.max(scaleX, scaleY)}) translate(${50 - (crop.x + crop.width / 2)}%, ${50 - (crop.y + crop.height / 2)}%)`,
  };
}
