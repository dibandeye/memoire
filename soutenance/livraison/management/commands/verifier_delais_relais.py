"""Vérifie chaque jour les colis stockés en point relais.

À planifier une fois par jour (cron / Planificateur de tâches Windows) :
    python manage.py verifier_delais_relais

- Jour 5 : rappel au client (2 jours restants)
- Jour 7 : dernier rappel au client (dernier jour)
- Jour 8+ : colis marqué « non récupéré », administrateurs et client notifiés
"""
from django.core.management.base import BaseCommand
from django.utils import timezone

from livraison.models import Livraison, Notification, PointRelais, Utilisateur


JOUR_RAPPEL_1 = 5
JOUR_RAPPEL_2 = PointRelais.DELAI_GARDE          # 7
JOUR_EXPIRATION = PointRelais.DELAI_GARDE + 1    # 8


def _jours_ecoules(livraison, aujourd_hui):
    """Nombre de jours calendaires depuis le dépôt (jour du dépôt = jour 0)."""
    return (aujourd_hui - timezone.localtime(livraison.date_depot_relais).date()).days


def _notifier(utilisateur, titre, message):
    Notification.objects.create(destinataire=utilisateur, titre=titre, message=message, canal='app')


class Command(BaseCommand):
    help = "Envoie les rappels J5/J7 et marque les colis non récupérés après J7 en point relais."

    def handle(self, *args, **options):
        aujourd_hui = timezone.localdate()
        admins = list(Utilisateur.objects.filter(role='admin'))
        compteurs = {'j5': 0, 'j7': 0, 'expires': 0}

        livraisons = Livraison.objects.filter(
            statut='deposee_relais', date_depot_relais__isnull=False
        ).select_related('client__utilisateur', 'point_relais')

        for livraison in livraisons:
            jours = _jours_ecoules(livraison, aujourd_hui)
            client = livraison.client.utilisateur
            relais = livraison.point_relais
            lieu = f"{relais.nom}, {relais.adresse}" if relais else "le point relais"
            date_limite = timezone.localtime(livraison.date_limite_recuperation).strftime('%d/%m/%Y')

            if jours >= JOUR_EXPIRATION:
                livraison.statut = 'non_recupere'
                livraison.save(update_fields=['statut'])
                _notifier(
                    client,
                    f"Colis #{livraison.id} non récupéré",
                    f"Le colis destiné à {livraison.nom_destinataire} n'a pas été récupéré à {lieu} "
                    f"dans le délai de {PointRelais.DELAI_GARDE} jours. Contactez-nous pour organiser la suite.",
                )
                for admin in admins:
                    _notifier(
                        admin,
                        f"Colis #{livraison.id} non récupéré",
                        f"Le colis #{livraison.id} (client : {client}, destinataire : "
                        f"{livraison.nom_destinataire} — {livraison.tel_destinataire}) a dépassé le délai "
                        f"de garde à {lieu}.",
                    )
                compteurs['expires'] += 1

            elif jours >= JOUR_RAPPEL_2 and not livraison.rappel_j7_envoye:
                _notifier(
                    client,
                    f"Dernier jour pour le colis #{livraison.id}",
                    f"Dernier rappel : le colis destiné à {livraison.nom_destinataire} doit être récupéré "
                    f"aujourd'hui ({date_limite}) à {lieu}. Horaires : {PointRelais.HORAIRES}. "
                    f"Pensez à prévenir le destinataire ({livraison.tel_destinataire}).",
                )
                livraison.rappel_j7_envoye = True
                livraison.save(update_fields=['rappel_j7_envoye'])
                compteurs['j7'] += 1

            elif JOUR_RAPPEL_1 <= jours < JOUR_RAPPEL_2 and not livraison.rappel_j5_envoye:
                _notifier(
                    client,
                    f"Rappel : colis #{livraison.id} en attente",
                    f"Le colis destiné à {livraison.nom_destinataire} attend toujours à {lieu}. "
                    f"Il reste {JOUR_RAPPEL_2 - jours} jours pour le récupérer (avant le {date_limite}). "
                    f"Horaires : {PointRelais.HORAIRES}. Pensez à prévenir le destinataire "
                    f"({livraison.tel_destinataire}).",
                )
                livraison.rappel_j5_envoye = True
                livraison.save(update_fields=['rappel_j5_envoye'])
                compteurs['j5'] += 1

        self.stdout.write(self.style.SUCCESS(
            f"Rappels J5 : {compteurs['j5']} — Rappels J7 : {compteurs['j7']} — "
            f"Colis non récupérés : {compteurs['expires']}"
        ))
