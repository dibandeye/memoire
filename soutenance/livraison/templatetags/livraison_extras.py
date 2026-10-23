from django import template
from django.conf import settings

register = template.Library()


@register.filter
def note_pleine(valeur):
    """Arrondit une note (ex : 4.3) au nombre d'étoiles pleines à afficher."""
    try:
        return int(round(float(valeur)))
    except (TypeError, ValueError):
        return 0


CLASSES_BADGE_STATUT = {
    'en_attente':         'badge-defaut',
    'attribuee':          'badge-attribuee',
    'en_cours_ramassage': 'badge-ramassage',
    'en_cours_livraison': 'badge-livraison',
    'deposee_relais':     'badge-livraison',
    'livree':             'badge-livree',
    'annulee':             'badge-annulee',
    'probleme':           'badge-probleme',
    'non_recupere':       'badge-annulee',
}


@register.filter
def classe_badge_statut(statut):
    """Renvoie la classe CSS du badge coloré correspondant à un statut de livraison."""
    return CLASSES_BADGE_STATUT.get(statut, 'badge-defaut')


@register.filter
def fcfa(montant):
    """Formate un montant avec séparateur de milliers : 2000 -> « 2 000 FCFA »."""
    try:
        return f"{int(round(float(montant))):,} FCFA".replace(',', '\u00a0')
    except (TypeError, ValueError):
        return f"{montant} FCFA"


@register.simple_tag
def compte_paiement(mode):
    """Numéro et nom du compte Wave / Orange Money qui reçoit les paiements."""
    return getattr(settings, 'PAIEMENT_MOBILE', {}).get(mode, {'numero': '—', 'nom': 'LivraisonExpress'})
