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

/** Extrait un message lisible d'une erreur HTTP DRF. */
export function describeError(err: unknown): string {
  const error = err as { error?: { detail?: string } | string; message?: string; status?: number };
  if (typeof error?.error === 'string') return error.error;
  if (error?.error?.detail) return error.error.detail;
  if (error?.status === 0) return 'Serveur injoignable.';
  return error?.message ?? 'Erreur inattendue.';
}
