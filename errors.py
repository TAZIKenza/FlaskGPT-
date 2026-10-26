"""Erreur "métier" : un message clair pour l'utilisateur + un code HTTP."""


class AppError(Exception):
    def __init__(self, message, status=500, code="error"):
        super().__init__(message)
        self.message = message  # texte affiché à l'utilisateur
        self.status = status    # code HTTP renvoyé (401, 429, 503...)
        self.code = code        # identifiant court, utile pour déboguer
