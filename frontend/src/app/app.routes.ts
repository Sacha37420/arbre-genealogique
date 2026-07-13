import { Routes } from '@angular/router';

import { SearchComponent } from './pages/search/search.component';
import { SettingsComponent } from './pages/settings/settings.component';
import { TreeComponent } from './pages/tree/tree.component';

export const routes: Routes = [
  { path: '', component: TreeComponent, title: 'Arbre' },
  { path: 'parametrage', component: SettingsComponent, title: 'Paramétrage des cartes' },
  { path: 'recherche', component: SearchComponent, title: 'Recherche & enrichissement' },
  { path: '**', redirectTo: '' },
];
