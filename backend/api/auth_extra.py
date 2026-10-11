"""
Authentification par jeton en paramètre d'URL, réservée aux fichiers médias.

Une balise <img src="…"> ne peut pas porter d'en-tête Authorization : sans cela,
aucune photo protégée ne s'afficherait dans l'arbre. On accepte donc le même JWT
Keycloak passé en « ?token= », validé exactement comme celui de l'en-tête
(signature JWKS, expiration, `azp`, groupes, claim email — par la même
fonction, `validate_token`) : seul son emplacement change.
"""
from .authentication import KeycloakJWTAuthentication


class KeycloakQueryTokenAuthentication(KeycloakJWTAuthentication):
    def authenticate(self, request):
        # L'en-tête reste prioritaire : le comportement standard est inchangé.
        if request.headers.get('Authorization', '').startswith('Bearer '):
            return super().authenticate(request)

        token = request.query_params.get('token')
        if not token:
            return None

        # Mêmes contrôles que l'en-tête, `azp` compris : une seule fonction.
        return self.validate_token(token)
