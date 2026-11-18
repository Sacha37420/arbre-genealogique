import { Injectable, computed, inject, signal } from '@angular/core';

import { ApiService } from './api.service';
import { Tree } from './models';

const STORAGE_KEY = 'arbre.currentTreeId';

/**
 * Arbre courant, partagé par les trois pages.
 *
 * Le choix est mémorisé dans le navigateur : passer de l'arbre au paramétrage
 * puis à la recherche ne doit pas obliger à re-sélectionner l'arbre à chaque fois.
 */
@Injectable({ providedIn: 'root' })
export class TreeStore {
  private api = inject(ApiService);

  readonly trees = signal<Tree[]>([]);
  readonly currentId = signal<number | null>(readStoredId());
  readonly loading = signal(false);
  readonly error = signal<string>('');

  readonly current = computed(() => {
    const id = this.currentId();
    return this.trees().find((t) => t.id === id) ?? null;
  });

  load(): void {
    this.loading.set(true);
    this.api.getTrees().subscribe({
      next: (trees) => {
        this.trees.set(trees);
        const stored = this.currentId();
        const stillExists = trees.some((t) => t.id === stored);
        if (!stillExists) {
          this.select(trees.length ? trees[0].id : null);
        }
        this.loading.set(false);
      },
      error: (err) => {
        this.error.set(describeError(err));
        this.loading.set(false);
      },
    });
  }

  select(id: number | null): void {
    this.currentId.set(id);
    if (id === null) {
      localStorage.removeItem(STORAGE_KEY);
    } else {
      localStorage.setItem(STORAGE_KEY, String(id));
    }
  }

  create(name: string): void {
    this.loading.set(true);
    this.api.createTree({ name }).subscribe({
      next: (tree) => {
        this.trees.update((trees) => [tree, ...trees]);
        this.select(tree.id);
        this.loading.set(false);
      },
      error: (err) => {
        this.error.set(describeError(err));
        this.loading.set(false);
      },
    });
  }
}

function readStoredId(): number | null {
  const raw = localStorage.getItem(STORAGE_KEY);
  const id = raw ? Number(raw) : NaN;
  return Number.isFinite(id) ? id : null;
}

/**
 * Extrait un message lisible d'une erreur HTTP DRF.
 *
 * DRF répond `{"detail": "…"}`, `["…"]` (ValidationError sur un texte) ou
 * `{"champ": ["…"]}` : les trois portent le message à montrer. Un corps qui
 * n'est pas un message (page HTML d'un proxy, trace) n'est jamais affiché, et
 * le message technique d'Angular (« Http failure response for <url> … ») non
 * plus : on retombe alors sur un texte qui dit quoi faire.
 */
export function describeError(err: unknown): string {
  const error = err as { error?: unknown; status?: number };
  const fromBody = messageFrom(error?.error);
  if (fromBody) return fromBody;
  switch (error?.status) {
    case 0:
      return 'Serveur injoignable. Vérifiez votre connexion, puis réessayez.';
    case 401:
      return 'Votre session a expiré : rechargez la page pour vous reconnecter.';
    case 403:
      return 'Vous n’avez pas le droit de faire cela sur cet arbre.';
    case 404:
      return 'Élément introuvable : il a peut-être été supprimé entre-temps.';
    case 413:
      return 'Fichier trop volumineux.';
  }
  if ((error?.status ?? 0) >= 500) {
    return 'Le serveur a rencontré un problème. Réessayez dans un instant.';
  }
  return 'Erreur inattendue. Réessayez.';
}

function messageFrom(body: unknown): string {
  if (typeof body === 'string') {
    const text = body.trim();
    return text && !text.startsWith('<') && text.length <= 300 ? text : '';
  }
  if (Array.isArray(body)) {
    return body.map(messageFrom).filter(Boolean).join(' ');
  }
  if (body && typeof body === 'object') {
    const detail = (body as { detail?: unknown }).detail;
    if (detail !== undefined) return messageFrom(detail);
    return Object.values(body).map(messageFrom).filter(Boolean).join(' ');
  }
  return '';
}
