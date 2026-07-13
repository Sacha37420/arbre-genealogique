import { Component, computed, effect, inject, signal } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { ActivatedRoute } from '@angular/router';

import { ApiService } from '../../core/api.service';
import { PersonCard, Provider, ProviderResult } from '../../core/models';
import { TreeStore, describeError } from '../../core/tree-store';

const CREDENTIALS_KEY = 'arbre.credentials';

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
 * Les clés d'API restent dans le navigateur (localStorage) et accompagnent chaque
 * requête : le serveur les relaie au fournisseur sans jamais les conserver. C'est
 * ce que veut le cahier des charges — les clés sont envoyées à l'endpoint.
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
  readonly providerKey = signal<string>('wikitree');
  readonly credentials = signal<Record<string, Record<string, string>>>(readCredentials());
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
    const stored = this.credentials()[provider.key] ?? {};
    return provider.required_credentials.some((key) => !stored[key]?.trim());
  });

  constructor() {
    this.store.load();

    this.api.getProviders().subscribe({
      next: (providers) => this.providers.set(providers),
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

  // ── Clés d'API ────────────────────────────────────────────────────────────
  credentialValue(providerKey: string, field: string): string {
    return this.credentials()[providerKey]?.[field] ?? '';
  }

  setCredential(providerKey: string, field: string, value: string): void {
    this.credentials.update((all) => {
      const next = { ...all, [providerKey]: { ...(all[providerKey] ?? {}), [field]: value } };
      localStorage.setItem(CREDENTIALS_KEY, JSON.stringify(next));
      return next;
    });
  }

  forgetKeys(providerKey: string): void {
    this.credentials.update((all) => {
      const next = { ...all };
      delete next[providerKey];
      localStorage.setItem(CREDENTIALS_KEY, JSON.stringify(next));
      return next;
    });
  }

  private currentCredentials(): Record<string, string> {
    return this.credentials()[this.providerKey()] ?? {};
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
        credentials: this.currentCredentials(),
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
    this.api.fetchProvider(result.provider, result.external_id, this.currentCredentials()).subscribe({
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
        credentials: this.currentCredentials(),
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
    this.api.geocodeTree(treeId, this.credentials()['nominatim'] ?? {}).subscribe({
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

function readCredentials(): Record<string, Record<string, string>> {
  try {
    return JSON.parse(localStorage.getItem(CREDENTIALS_KEY) ?? '{}');
  } catch {
    return {};
  }
}
