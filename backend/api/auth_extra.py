"""
Authentification par jeton en paramètre d'URL, réservée aux fichiers médias.

Une balise <img src="…"> ne peut pas porter d'en-tête Authorization : sans cela,
aucune photo protégée ne s'afficherait dans l'arbre. On accepte donc le même JWT
Keycloak passé en « ?token= », validé exactement comme celui de l'en-tête
(signature JWKS, expiration, claim email) — seul son emplacement change.
"""
import jwt
from jwt import InvalidTokenError
from rest_framework.exceptions import AuthenticationFailed

from .authentication import KeycloakJWTAuthentication, KeycloakUser


class KeycloakQueryTokenAuthentication(KeycloakJWTAuthentication):
    def authenticate(self, request):
        # L'en-tête reste prioritaire : le comportement standard est inchangé.
        if request.headers.get('Authorization', '').startswith('Bearer '):
            return super().authenticate(request)

        token = request.query_params.get('token')
        if not token:
            return None

        try:
            signing_key = self._get_jwks_client().get_signing_key_from_jwt(token)
            claims = jwt.decode(
                token,
                signing_key.key,
                algorithms=['RS256'],
                options={'verify_exp': True, 'verify_aud': False},
            )
        except InvalidTokenError as exc:
            raise AuthenticationFailed(f'Token invalide : {exc}') from exc

        if not claims.get('email'):
            raise AuthenticationFailed("Le token ne contient pas de claim 'email'.")

        return KeycloakUser(claims), token
