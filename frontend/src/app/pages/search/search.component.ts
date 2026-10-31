import { Component, computed, effect, inject, signal } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { ActivatedRoute } from '@angular/router';

import { ApiService } from '../../core/api.service';
import { PersonCard, Provider, ProviderResult } from '../../core/models';
import { TreeStore, describeError } from '../../core/tree-store';

/** Ancien emplacement des clés (localStorage) : effacé à l'ouverture, jamais lu ni envoyé. */
const LEGACY_CREDENTIALS_KEY = 'arbre.credentials';

const RELATION_LABELS: Record<string, string> = {
  FATHER: 'Père',
  MOTHER: 'Mère',
  SPOUSE: 'Conjoint·e',
  CHILD: 'Enfant',
  SIBLING: 'Frère/Sœur',
};

/**
 * Recherche et enrichissement.
 *
 * Les clés d'API sont dans le coffre du serveur, chiffrées, au nom de
 * l'utilisateur (`/api/provider-credentials/<fournisseur>/`). Le navigateur les
 * dépose (PUT) et ne les relit jamais : il ne connaît que « configurée / non
 * configurée ». Les appels aux fournisseurs ne portent aucune clé — le serveur
 * lit le coffre. Rien n'est gardé dans localStorage ni sessionStorage : une XSS
 * ne peut pas y prendre les clés.
 */
@Component({
  selector: 'app-search',
  standalone: true,
  imports: [FormsModule],
  templateUrl: './search.component.html',
  styleUrl: './search.component.scss',
})
export class SearchComponent {
  private api = inject(ApiService);
  private route = inject(ActivatedRoute);
  readonly store = inject(TreeStore);

  readonly providers = signal<Provider[]>([]);
  // Le fichier des décès est la seule source d'état civil officiel accessible sans
  // clé : c'est celle qui a le plus de chances de reconnaître un ancêtre ordinaire,
  // là où les arbres collaboratifs ne contiennent que ce qu'un bénévole a saisi.
  readonly providerKey = signal<string>('deces-insee');
  /** État du coffre : fournisseur → champ → configuré ? (jamais la valeur). */
  readonly configured = signal<Record<string, Record<string, boolean>>>({});
  /** Saisie en cours, en mémoire seulement, vidée dès l'envoi. */
  readonly drafts = signal<Record<string, string>>({});
  readonly savingKey = signal('');
  readonly showKeys = signal(false);

  readonly given = signal('');
  readonly surname = signal('');
  readonly birthYear = signal<number | null>(null);
  readonly deathYear = signal<number | null>(null);
  readonly place = signal('');

  readonly results = signal<ProviderResult[]>([]);
  readonly detail = signal<ProviderResult | null>(null);
  readonly searching = signal(false);
  readonly importing = signal('');
  readonly message = signal('');
  readonly error = signal('');

  /** Individu de l'arbre à compléter ; vide = créer une nouvelle personne. */
  readonly target = signal<number | null>(null);
  readonly people = signal<PersonCard[]>([]);
  readonly withRelatives = signal(true);
  readonly withPhoto = signal(true);

  relationLabel(relation: string): string {
    return RELATION_LABELS[relation] ?? relation;
  }

  readonly provider = computed(
    () => this.providers().find((p) => p.key === this.providerKey()) ?? null,
  );

  /** Fournisseurs interrogeables : le géocodeur ne cherche pas de personnes. */
  readonly searchable = computed(() => this.providers().filter((p) => p.supports_search));

  readonly missingKey = computed(() => {
    const provider = this.provider();
    if (!provider?.requires_key) return false;
    const state = this.configured()[provider.key] ?? {};
    return provider.required_credentials.some((key) => !state[key]);
  });

  constructor() {
    // Les anciennes clés du navigateur sont effacées sans être envoyées : chacun
    // ressaisit la sienne une fois, dans le coffre.
    localStorage.removeItem(LEGACY_CREDENTIALS_KEY);

    this.store.load();

    this.api.getProviders().subscribe({
      next: (providers) => {
        this.providers.set(providers);
        for (const provider of providers) {
          if (credentialFields(provider).length) this.refreshCredentials(provider.key);
        }
      },
      error: (err) => this.error.set(describeError(err)),
    });

    // Arrivée depuis la grande carte : le formulaire est pré-rempli avec la personne.
    this.route.queryParamMap.subscribe((params) => {
      const individual = params.get('individual');
      if (individual) this.target.set(Number(individual));
      if (params.get('surname')) this.surname.set(params.get('surname')!);
      if (params.get('given')) this.given.set(params.get('given')!);
    });

    effect(() => {
      const treeId = this.store.currentId();
      if (treeId === null) {
        this.people.set([]);
        return;
      }
      this.api.getGraph(treeId).subscribe({
        next: (graph) => this.people.set(graph.nodes),
        error: () => this.people.set([]),
      });
    });
  }

  // ── Clés d'API (coffre du serveur) ──────────────────────────────────────
  fieldsOf(provider: Provider): string[] {
    return credentialFields(provider);
  }

  isConfigured(providerKey: string, field: string): boolean {
    return this.configured()[providerKey]?.[field] ?? false;
  }

  draft(providerKey: string, field: string): string {
    return this.drafts()[`${providerKey}.${field}`] ?? '';
  }

  setDraft(providerKey: string, field: string, value: string): void {
    this.drafts.update((all) => ({ ...all, [`${providerKey}.${field}`]: value }));
  }

  /** Dépose la clé saisie dans le coffre, puis l'oublie côté navigateur. */
  saveCredential(providerKey: string, field: string): void {
    const value = this.draft(providerKey, field).trim();
    if (!value) return;
    this.writeCredentials(providerKey, { [field]: value });
  }

  /** « Oublier » : chaîne vide pour chaque champ du fournisseur ⇒ supprimé du coffre. */
  forgetKeys(provider: Provider): void {
    const body: Record<string, string> = {};
    for (const field of credentialFields(provider)) body[field] = '';
    this.writeCredentials(provider.key, body);
  }

  private writeCredentials(providerKey: string, body: Record<string, string>): void {
    this.savingKey.set(providerKey);
    this.error.set('');
    this.api.putProviderCredentials(providerKey, body).subscribe({
      next: () => {
        this.savingKey.set('');
        this.drafts.update((all) => {
          const next = { ...all };
          for (const field of Object.keys(body)) delete next[`${providerKey}.${field}`];
          return next;
        });
        this.refreshCredentials(providerKey);
      },
      error: (err) => {
        this.savingKey.set('');
        this.error.set(describeError(err));
      },
    });
  }

  private refreshCredentials(providerKey: string): void {
    this.api.getProviderCredentials(providerKey).subscribe({
      next: (status) => {
        const state: Record<string, boolean> = {};
        for (const [key, value] of Object.entries(status)) {
          if (key.endsWith('_configured')) state[key.slice(0, -'_configured'.length)] = value;
        }
        this.configured.update((all) => ({ ...all, [providerKey]: state }));
      },
      error: () => this.configured.update((all) => ({ ...all, [providerKey]: {} })),
    });
  }

  // ── Recherche ─────────────────────────────────────────────────────────────
  search(): void {
    if (!this.given().trim() && !this.surname().trim()) {
      this.error.set('Renseignez au moins un prénom ou un nom.');
      return;
    }

    this.searching.set(true);
    this.error.set('');
    this.message.set('');
    this.detail.set(null);

    this.api
      .searchProvider({
        provider: this.providerKey(),
        tree: this.store.currentId() ?? undefined,
        query: {
          given_name: this.given().trim(),
          surname: this.surname().trim(),
          birth_year: this.birthYear(),
          death_year: this.deathYear(),
          place: this.place().trim(),
          limit: 15,
        },
        save: this.store.currentId() !== null,
      })
      .subscribe({
        next: (response) => {
          this.results.set(response.results);
          this.searching.set(false);
          if (!response.results.length) {
            this.message.set('Aucun résultat. Élargissez la recherche ou changez de source.');
          }
        },
        error: (err) => {
          this.searching.set(false);
          this.results.set([]);
          this.error.set(describeError(err));
        },
      });
  }

  openDetail(result: ProviderResult): void {
    this.detail.set(result);
    this.error.set('');

    // La recherche ne renvoie pas les proches : il faut la fiche complète.
    this.api.fetchProvider(result.provider, result.external_id).subscribe({
      next: (full) => this.detail.set(full),
      error: (err) => this.error.set(describeError(err)),
    });
  }

  closeDetail(): void {
    this.detail.set(null);
  }

  // ── Import ────────────────────────────────────────────────────────────────
  importResult(result: ProviderResult): void {
    const treeId = this.store.currentId();
    if (treeId === null) {
      this.error.set('Sélectionnez d’abord un arbre.');
      return;
    }

    this.importing.set(result.external_id);
    this.error.set('');
    this.message.set('');

    this.api
      .importFromProvider({
        provider: result.provider,
        external_id: result.external_id,
        tree: treeId,
        target: this.target(),
        with_relatives: this.withRelatives(),
        with_photo: this.withPhoto(),
      })
      .subscribe({
        next: (response) => {
          const created = (response as { relatives_created?: unknown[] }).relatives_created ?? [];
          this.importing.set('');
          this.message.set(
            this.target()
              ? `Fiche fusionnée dans la personne sélectionnée${created.length ? `, ${created.length} proche(s) ajouté(s)` : ''}.`
              : `Personne ajoutée à l’arbre${created.length ? ` avec ${created.length} proche(s)` : ''}.`,
          );
          this.target.set(null);
          this.store.load();
        },
        error: (err) => {
          this.importing.set('');
          this.error.set(describeError(err));
        },
      });
  }

  geocodeAll(): void {
    const treeId = this.store.currentId();
    if (treeId === null) return;

    this.message.set('Géocodage en cours…');
    this.api.geocodeTree(treeId).subscribe({
      next: (response) => {
        this.message.set(
          `${response.geocoded} lieu(x) géocodé(s), ${response.not_found} introuvable(s).`,
        );
      },
      error: (err) => {
        this.message.set('');
        this.error.set(describeError(err));
      },
    });
  }

  labelOf(card: PersonCard): string {
    return `${card.full_name || 'Sans nom'}${card.birth_year ? ` (${card.birth_year})` : ''}`;
  }

  scorePercent(result: ProviderResult): number {
    return Math.round(result.score * 100);
  }
}

/** Champs du coffre d'un fournisseur : obligatoires puis facultatifs (comme le serveur). */
function credentialFields(provider: Provider): string[] {
  return provider.required_credentials.concat(provider.optional_credentials ?? []);
}
