"""Tests backend d'arbre-genealogique.

Lancement : ``python manage.py test`` (rien d'autre). Aucun réseau n'est
nécessaire : Keycloak (JWKS), ``storage`` et les fournisseurs généalogiques sont
simulés — voir ``helpers.py``.

    test_serrure.py       Verrou 2 : jetons RS256 réels contre la classe
                          d'authentification réelle (en-tête et « ?token= »).
    test_autorisation.py  Qui lit, modifie, supprime quoi : propriétaire,
                          éditeur, lecteur, étranger, arbre public — sur chaque
                          ressource, les partages, les imports.
    test_medias.py        Médias dans ``storage`` : espace par arbre, purge par
                          signal, URL directe ou proxy, synchronisation des
                          membres.
    test_metier.py        Dates GEDCOM, familles, vivants/décédés, générations,
                          styles conditionnels, import/export, enrichissement.

Un test marqué ``expectedFailure`` montre un défaut du code de production,
décrit dans son commentaire : il redeviendra « succès inattendu » (donc rouge)
le jour où le défaut sera corrigé — retirer alors le décorateur.
"""
