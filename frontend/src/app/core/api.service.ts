import { HttpClient } from '@angular/common/http';
import { Injectable, inject } from '@angular/core';
import { Observable, map } from 'rxjs';

import {
  CardTemplate,
  EventRecord,
  EventType,
  IndividualDetail,
  Provider,
  ProviderResult,
  Relations,
  SearchPayload,
  Timeline,
  Tree,
  TreeGraph,
  TreeShare,
  TreeViewSettings,
} from './models';

interface EnvWindow {
  __env?: { apiUrl?: string };
}

@Injectable({ providedIn: 'root' })
export class ApiService {
  private http = inject(HttpClient);

  private get base(): string {
    return (window as unknown as EnvWindow).__env?.apiUrl ?? 'http://localhost:8090';
  }

  /**
   * Adresse d'une photo, SANS jeton : à passer à `[appAuthSrc]` (lecture par
   * HttpClient + Blob, jeton dans l'en-tête — `core/auth-blob.service.ts`),
   * jamais à un `[src]` brut.
   *
   * Deux formes : une URL storage directe (arbre privé, cf.
   * `storage_client.media_url` côté backend) ou le chemin du proxy
   * `/api/media/<id>/file/` (arbre public, repli sans DOMAIN), préfixé ici par
   * la base de l'API. Un média externe garde son adresse d'origine.
   */
  mediaUrl(path: string | null): string {
    if (!path) return '';
    if (/^https?:\/\//.test(path)) return path;
    return `${this.base}${path}`;
  }

  // ── Arbres ────────────────────────────────────────────────────────────────
  getTrees(): Observable<Tree[]> {
    return this.list<Tree>(`${this.base}/api/trees/`);
  }

  createTree(payload: Partial<Tree>): Observable<Tree> {
    return this.http.post<Tree>(`${this.base}/api/trees/`, payload);
  }

  // ── Partage ───────────────────────────────────────────────────────────────
  getShares(treeId: number): Observable<TreeShare[]> {
    return this.list<TreeShare>(`${this.base}/api/tree-shares/?tree=${treeId}`);
  }

  /** Inviter quelqu'un déjà invité change son rôle : le serveur fait un upsert. */
  addShare(treeId: number, email: string, role: TreeShare['role']): Observable<TreeShare> {
    return this.http.post<TreeShare>(`${this.base}/api/tree-shares/`, {
      tree: treeId,
      email,
      role,
    });
  }

  updateShare(shareId: number, role: TreeShare['role']): Observable<TreeShare> {
    return this.http.patch<TreeShare>(`${this.base}/api/tree-shares/${shareId}/`, { role });
  }

  removeShare(shareId: number): Observable<unknown> {
    return this.http.delete(`${this.base}/api/tree-shares/${shareId}/`);
  }

  getGraph(treeId: number): Observable<TreeGraph> {
    return this.http.get<TreeGraph>(`${this.base}/api/trees/${treeId}/graph/`);
  }

  getSettings(treeId: number): Observable<TreeViewSettings> {
    return this.http.get<TreeViewSettings>(`${this.base}/api/trees/${treeId}/settings/`);
  }

  patchSettings(treeId: number, payload: Partial<TreeViewSettings>): Observable<TreeViewSettings> {
    return this.http.patch<TreeViewSettings>(`${this.base}/api/trees/${treeId}/settings/`, payload);
  }

  /** Enregistre en un seul appel toutes les cartes déplacées à la souris. */
  saveLayout(
    treeId: number,
    positions: { individual?: number; family?: number; x: number; y: number; pinned: boolean }[],
  ): Observable<{ saved: number }> {
    return this.http.post<{ saved: number }>(`${this.base}/api/trees/${treeId}/layout/`, {
      positions,
    });
  }

  importGedcom(treeId: number, file: File): Observable<unknown> {
    const form = new FormData();
    form.append('file', file);
    return this.http.post(`${this.base}/api/trees/${treeId}/import_gedcom/`, form);
  }

  exportGedcom(treeId: number): Observable<Blob> {
    return this.http.get(`${this.base}/api/trees/${treeId}/gedcom/`, { responseType: 'blob' });
  }

  /** La clé Geoapify éventuelle est lue par le serveur dans le coffre de l'utilisateur. */
  geocodeTree(treeId: number): Observable<{ geocoded: number; not_found: number }> {
    return this.http.post<{ geocoded: number; not_found: number }>(
      `${this.base}/api/trees/${treeId}/geocode/`,
      {},
    );
  }

  // ── Individus ─────────────────────────────────────────────────────────────
  getTimeline(individualId: number): Observable<Timeline> {
    return this.http.get<Timeline>(`${this.base}/api/individuals/${individualId}/timeline/`);
  }

  getIndividual(id: number): Observable<IndividualDetail> {
    return this.http.get<IndividualDetail>(`${this.base}/api/individuals/${id}/`);
  }

  /** Événements d'une famille (mariage, divorce…) : ils figurent sur la frise de ses conjoints. */
  getFamilyEvents(familyId: number): Observable<EventRecord[]> {
    return this.list<EventRecord>(`${this.base}/api/events/?family=${familyId}`);
  }

  // ── Relations ─────────────────────────────────────────────────────────────
  getRelations(individualId: number): Observable<Relations> {
    return this.http.get<Relations>(`${this.base}/api/individuals/${individualId}/relations/`);
  }

  /**
   * Ajoute un proche. Le serveur crée la famille qui porte le lien si besoin —
   * un parent va dans la famille où la personne est enfant, un enfant dans celle
   * où elle est conjoint : deux familles différentes selon le sens du lien.
   */
  addRelative(
    individualId: number,
    payload: {
      relation: 'PARENT' | 'SPOUSE' | 'CHILD' | 'SIBLING';
      individual?: number;
      givn?: string;
      surn?: string;
      sex?: string;
      pedigree?: string;
    },
  ): Observable<Relations> {
    return this.http.post<Relations>(
      `${this.base}/api/individuals/${individualId}/add_relative/`,
      payload,
    );
  }

  removeSpouseLink(linkId: number): Observable<unknown> {
    return this.http.delete(`${this.base}/api/family-spouses/${linkId}/`);
  }

  removeChildLink(linkId: number): Observable<unknown> {
    return this.http.delete(`${this.base}/api/family-children/${linkId}/`);
  }

  updateChildLink(linkId: number, payload: { pedigree?: string }): Observable<unknown> {
    return this.http.patch(`${this.base}/api/family-children/${linkId}/`, payload);
  }

  updateFamily(id: number, payload: { union_type?: string }): Observable<unknown> {
    return this.http.patch(`${this.base}/api/families/${id}/`, payload);
  }

  createIndividual(payload: {
    tree: number;
    givn?: string;
    surn?: string;
    sex?: string;
  }): Observable<{ id: number }> {
    return this.http.post<{ id: number }>(`${this.base}/api/individuals/`, payload);
  }

  updateIndividual(id: number, payload: Record<string, unknown>): Observable<unknown> {
    return this.http.patch(`${this.base}/api/individuals/${id}/`, payload);
  }

  deleteIndividual(id: number): Observable<unknown> {
    return this.http.delete(`${this.base}/api/individuals/${id}/`);
  }

  // ── Familles ──────────────────────────────────────────────────────────────
  createFamily(treeId: number): Observable<{ id: number }> {
    return this.http.post<{ id: number }>(`${this.base}/api/families/`, { tree: treeId });
  }

  addSpouse(familyId: number, individual: number): Observable<unknown> {
    return this.http.post(`${this.base}/api/families/${familyId}/add_spouse/`, { individual });
  }

  addChild(familyId: number, individual: number): Observable<unknown> {
    return this.http.post(`${this.base}/api/families/${familyId}/add_child/`, { individual });
  }

  // ── Événements ────────────────────────────────────────────────────────────
  getEventTypes(): Observable<EventType[]> {
    return this.http.get<EventType[]>(`${this.base}/api/events/types/`);
  }

  createEvent(payload: Record<string, unknown>): Observable<EventRecord> {
    return this.http.post<EventRecord>(`${this.base}/api/events/`, payload);
  }

  updateEvent(id: number, payload: Record<string, unknown>): Observable<EventRecord> {
    return this.http.patch<EventRecord>(`${this.base}/api/events/${id}/`, payload);
  }

  deleteEvent(id: number): Observable<unknown> {
    return this.http.delete(`${this.base}/api/events/${id}/`);
  }

  // ── Médias ────────────────────────────────────────────────────────────────
  uploadPhoto(treeId: number, individualId: number, file: File): Observable<unknown> {
    const form = new FormData();
    form.append('file', file);
    form.append('tree', String(treeId));
    form.append('individual', String(individualId));
    return this.http.post(`${this.base}/api/media/`, form);
  }

  /** Désigne une photo de la galerie comme portrait. Le serveur déclasse l'ancienne. */
  setPrimaryPhoto(linkId: number): Observable<unknown> {
    return this.http.patch(`${this.base}/api/media-links/${linkId}/`, { is_primary: true });
  }

  /** Retire une photo de la personne (et le média lui-même s'il ne sert plus ailleurs). */
  removePhoto(linkId: number): Observable<unknown> {
    return this.http.delete(`${this.base}/api/media-links/${linkId}/`);
  }

  // ── Gabarits de cartes ────────────────────────────────────────────────────
  getCardTemplates(treeId: number): Observable<CardTemplate[]> {
    return this.list<CardTemplate>(`${this.base}/api/card-templates/?tree=${treeId}`);
  }

  updateCardTemplate(id: number, payload: Partial<CardTemplate>): Observable<CardTemplate> {
    return this.http.patch<CardTemplate>(`${this.base}/api/card-templates/${id}/`, payload);
  }

  // ── Styles ────────────────────────────────────────────────────────────────
  getNodeStyles(treeId: number): Observable<Record<string, unknown>[]> {
    return this.list(`${this.base}/api/node-styles/?tree=${treeId}`);
  }

  updateNodeStyle(id: number, payload: Record<string, unknown>): Observable<unknown> {
    return this.http.patch(`${this.base}/api/node-styles/${id}/`, payload);
  }

  getStyleRules(treeId: number): Observable<Record<string, unknown>[]> {
    return this.list(`${this.base}/api/style-rules/?tree=${treeId}`);
  }

  updateStyleRule(id: number, payload: Record<string, unknown>): Observable<unknown> {
    return this.http.patch(`${this.base}/api/style-rules/${id}/`, payload);
  }

  getEdgeStyles(treeId: number): Observable<Record<string, unknown>[]> {
    return this.list(`${this.base}/api/edge-styles/?tree=${treeId}`);
  }

  updateEdgeStyle(id: number, payload: Record<string, unknown>): Observable<unknown> {
    return this.http.patch(`${this.base}/api/edge-styles/${id}/`, payload);
  }

  // ── Enrichissement ────────────────────────────────────────────────────────
  getProviders(): Observable<Provider[]> {
    return this.http.get<Provider[]>(`${this.base}/api/enrich/providers/`);
  }

  /** Coffre : `{"<champ>_configured": bool}` — jamais la valeur. */
  getProviderCredentials(provider: string): Observable<Record<string, boolean>> {
    return this.http.get<Record<string, boolean>>(
      `${this.base}/api/provider-credentials/${encodeURIComponent(provider)}/`,
    );
  }

  /** Dépose (ou, avec une chaîne vide, oublie) des clés dans le coffre. */
  putProviderCredentials(provider: string, body: Record<string, string>): Observable<void> {
    return this.http.put<void>(
      `${this.base}/api/provider-credentials/${encodeURIComponent(provider)}/`,
      body,
    );
  }

  /** Aucune clé d'API dans la requête : le serveur lit le coffre de l'utilisateur. */
  searchProvider(
    payload: SearchPayload,
  ): Observable<{ provider: string; count: number; results: ProviderResult[] }> {
    return this.http.post<{ provider: string; count: number; results: ProviderResult[] }>(
      `${this.base}/api/enrich/search/`,
      payload,
    );
  }

  fetchProvider(provider: string, externalId: string): Observable<ProviderResult> {
    return this.http.post<ProviderResult>(`${this.base}/api/enrich/fetch/`, {
      provider,
      external_id: externalId,
    });
  }

  importFromProvider(payload: {
    provider: string;
    external_id: string;
    tree: number;
    target?: number | null;
    with_relatives?: boolean;
    with_photo?: boolean;
  }): Observable<unknown> {
    return this.http.post(`${this.base}/api/enrich/import/`, payload);
  }

  /** DRF renvoie une liste nue ou un objet paginé selon la configuration. */
  private list<T>(url: string): Observable<T[]> {
    return this.http
      .get<T[] | { results?: T[] }>(url)
      .pipe(map((response) => (Array.isArray(response) ? response : (response.results ?? []))));
  }
}
