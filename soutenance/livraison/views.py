import json
from datetime import timedelta
from functools import wraps

from django.contrib import messages
from django.contrib.auth import authenticate, login, logout, update_session_auth_hash
from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.db.models import Count, Q, Sum
from django.http import JsonResponse
from django.shortcuts import render, redirect, get_object_or_404
from django.urls import reverse
from django.utils import timezone
from django.utils.dateparse import parse_datetime

from .models import (
    Utilisateur, Client, Livreur, Administrateur,
    Livraison, Message, Evaluation, Notification, Vehicule, LivraisonExclusion, PointRelais,
    GerantPointRelais, PositionLivreur,
)
from .templatetags.livraison_extras import fcfa


def _parser_datetime(valeur):
    """Convertit la valeur d'un <input type="datetime-local"> en datetime aware, ou None."""
    if not valeur:
        return None
    date = parse_datetime(valeur)
    if date is None:
        return None
    if timezone.is_naive(date):
        date = timezone.make_aware(date)
    return date


# ── OUTILS D'ACCÈS ─────────────────────────────────────────────────
def role_required(*roles):
    def decorateur(vue):
        @wraps(vue)
        @login_required
        def enveloppe(request, *args, **kwargs):
            if request.user.role not in roles:
                messages.error(request, "Accès non autorisé.")
                return _redirection_selon_role(request.user)
            return vue(request, *args, **kwargs)
        return enveloppe
    return decorateur


def _redirection_selon_role(user):
    if user.role == 'client':
        return redirect('livraison:dashboard_client')
    if user.role == 'livreur':
        return redirect('livraison:dashboard_livreur')
    if user.role == 'agent_relais':
        return redirect('livraison:dashboard_gerant')
    return redirect('livraison:dashboard_admin')


def _url_retour(request, secours):
    next_url = request.POST.get('next') or request.GET.get('next')
    if next_url and next_url.startswith('/'):
        return next_url
    return secours


def _peut_voir_livraison(user, livraison):
    if user.role == 'admin':
        return True
    if user.role == 'client':
        return livraison.client.utilisateur_id == user.id
    if user.role == 'livreur':
        return bool(livraison.livreur_id) and livraison.livreur.utilisateur_id == user.id
    return False


PROGRESSION_STATUTS = {
    'attribuee': 'en_cours_ramassage',
    'en_cours_ramassage': 'en_cours_livraison',
    'en_cours_livraison': 'livree',
}



def _prochain_statut(livraison):
    """Un colis « point relais » est déposé au relais au lieu d'être livré."""
    if livraison.statut == 'en_cours_livraison' and livraison.type_livraison == 'point_relais':
        return 'deposee_relais'
    return PROGRESSION_STATUTS.get(livraison.statut)


PERMIS_REQUIS = {
    'moto':    ('moto', 'les_deux'),
    'voiture': ('voiture', 'les_deux'),
}


def _permis_compatible(type_vehicule, type_permis):
    return type_permis in PERMIS_REQUIS.get(type_vehicule, ())


def _vehicule_operationnel(livreur):
    """Renvoie (type_vehicule, capacite_charge) du véhicule utilisé par ce livreur,
    ou (None, None) s'il n'en a aucun."""
    vehicule = getattr(livreur, 'vehicule', None)
    if vehicule:
        return vehicule.type_vehicule, vehicule.capacite_charge
    if livreur.possede_vehicule_perso:
        return livreur.type_vehicule_perso, livreur.capacite_charge_perso
    return None, None


def _types_vehicules_livreur(livreur):
    """Types de véhicules dont dispose le livreur (entreprise et/ou personnel)."""
    types = set()
    vehicule = getattr(livreur, 'vehicule', None)
    if vehicule:
        types.add(vehicule.type_vehicule)
    if livreur.possede_vehicule_perso and livreur.type_vehicule_perso:
        types.add(livreur.type_vehicule_perso)
    return types


def _raison_incompatibilite(livreur, type_vehicule):
    """None si le livreur dispose du type de véhicule demandé, sinon un message d'erreur explicite."""
    types = _types_vehicules_livreur(livreur)
    if not types:
        return "Ce livreur ne dispose d'aucun véhicule opérationnel."
    if type_vehicule not in types:
        libelle = dict(Livraison.TYPE_VEHICULE).get(type_vehicule, type_vehicule).lower()
        return f"Cette livraison nécessite un livreur équipé d'un véhicule de type {libelle}."
    return None


def get_livreurs_compatibles(livraison):
    """Livreurs actifs et disponibles disposant du type de véhicule demandé
    (véhicule entreprise ou personnel), hors livreurs exclus de cette livraison
    (ex : annulation pour position)."""
    return Livreur.objects.filter(
        est_disponible=True, utilisateur__est_actif=True,
    ).filter(
        Q(vehicule__type_vehicule=livraison.type_vehicule)
        | Q(possede_vehicule_perso=True, type_vehicule_perso=livraison.type_vehicule)
    ).exclude(
        livraisons_exclues__livraison=livraison
    ).select_related('utilisateur', 'vehicule').distinct()


# ── AUTHENTIFICATION ────────────────────────────────────────────────
def connexion(request):
    if request.user.is_authenticated:
        return _redirection_selon_role(request.user)

    if request.method == 'POST':
        email = request.POST.get('email', '').strip().lower()
        mot_de_passe = request.POST.get('mot_de_passe', '')
        utilisateur = authenticate(request, username=email, password=mot_de_passe)
        if utilisateur is None:
            messages.error(request, "Email ou mot de passe incorrect.")
        elif not utilisateur.est_actif:
            messages.warning(request, "Votre compte est en attente de validation par un administrateur.")
        else:
            login(request, utilisateur)
            if utilisateur.role == 'agent_relais':
                request.session['mdp_provisoire'] = (mot_de_passe.lower() == utilisateur.email.lower())
            return _redirection_selon_role(utilisateur)

    return render(request, 'connexion.html')


def inscription(request):
    if request.user.is_authenticated:
        return _redirection_selon_role(request.user)

    if request.method == 'POST':
        role = request.POST.get('role')
        nom = request.POST.get('nom', '').strip()
        prenom = request.POST.get('prenom', '').strip()
        email = request.POST.get('email', '').strip().lower()
        telephone = request.POST.get('telephone', '').strip()
        mot_de_passe = request.POST.get('mot_de_passe', '')
        mot_de_passe_confirm = request.POST.get('mot_de_passe_confirm', '')

        erreurs = []
        erreurs_champs = {}
        if role not in ('client', 'livreur'):
            erreurs.append("Rôle invalide.")
        if not nom or not prenom or not email or not mot_de_passe:
            erreurs.append("Tous les champs obligatoires doivent être remplis.")
        if mot_de_passe != mot_de_passe_confirm:
            erreurs.append("Les mots de passe ne correspondent pas.")
        if Utilisateur.objects.filter(email=email).exists():
            erreurs.append("Cet email est déjà utilisé.")

        type_permis = request.POST.get('type_permis', 'moto')
        possede_vehicule_perso = False
        type_vehicule_perso = None
        immatriculation_perso = ''
        capacite_charge_perso = 0.0

        if role == 'livreur':
            possede_vehicule_perso = request.POST.get('possede_vehicule_perso') == 'on'
            if possede_vehicule_perso:
                type_vehicule_perso = request.POST.get('type_vehicule_perso', '').strip()
                immatriculation_perso = request.POST.get('immatriculation_perso', '').strip()
                try:
                    capacite_charge_perso = float(request.POST.get('capacite_charge_perso') or 0)
                except ValueError:
                    capacite_charge_perso = 0.0

                if type_vehicule_perso not in ('moto', 'voiture'):
                    erreurs_champs['type_vehicule_perso'] = "Veuillez choisir un type de véhicule."
                elif type_vehicule_perso == 'moto' and type_permis not in ('moto', 'les_deux'):
                    erreurs_champs['type_vehicule_perso'] = "Votre permis ne vous autorise pas à déclarer une moto."
                elif type_vehicule_perso == 'voiture' and type_permis not in ('voiture', 'les_deux'):
                    erreurs_champs['type_vehicule_perso'] = "Votre permis ne vous autorise pas à déclarer une voiture."

                if not immatriculation_perso:
                    erreurs_champs['immatriculation_perso'] = "L'immatriculation est obligatoire pour un véhicule personnel."

                if capacite_charge_perso <= 0:
                    erreurs_champs['capacite_charge_perso'] = "La capacité de charge doit être supérieure à 0."

        if erreurs or erreurs_champs:
            for erreur in erreurs:
                messages.error(request, erreur)
            return render(request, 'inscription.html', {'donnees': request.POST, 'erreurs_champs': erreurs_champs})

        utilisateur = Utilisateur.objects.create_user(
            email=email, password=mot_de_passe,
            nom=nom, prenom=prenom, telephone=telephone, role=role,
            est_actif=(role == 'client'),
        )

        if role == 'client':
            Client.objects.create(
                utilisateur=utilisateur,
                adresse=request.POST.get('adresse', '').strip(),
                ville=request.POST.get('ville', '').strip(),
            )
            messages.success(request, "Inscription réussie, vous pouvez vous connecter.")
        else:
            Livreur.objects.create(
                utilisateur=utilisateur,
                num_permis=request.POST.get('num_permis', '').strip(),
                type_permis=type_permis,
                possede_vehicule_perso=possede_vehicule_perso,
                type_vehicule_perso=type_vehicule_perso if possede_vehicule_perso else None,
                immatriculation_perso=immatriculation_perso if possede_vehicule_perso else None,
                capacite_charge_perso=capacite_charge_perso if possede_vehicule_perso else 0.0,
            )
            messages.success(
                request,
                "Inscription réussie. Votre compte doit être validé par un administrateur "
                "avant de pouvoir vous connecter."
            )
        return redirect('livraison:connexion')

    return render(request, 'inscription.html')


@login_required
def deconnexion(request):
    logout(request)
    messages.info(request, "Vous avez été déconnecté.")
    return redirect('livraison:connexion')


# ── ESPACE CLIENT ────────────────────────────────────────────────────
STATUTS_EN_COURS_CLIENT = ['attribuee', 'en_cours_ramassage', 'en_cours_livraison', 'deposee_relais']

STATUT_FILTRES_CLIENT = {
    'en_attente': ['en_attente'],
    'en_cours':   STATUTS_EN_COURS_CLIENT,
    'livrees':    ['livree'],
    'annulees':   ['annulee', 'probleme'],
}


def _contexte_client(client, section):
    """Contexte commun à toutes les pages de l'espace client (sidebar, header)."""
    return {
        'section': section,
        'nb_notifs_non_lues': client.utilisateur.notifications.filter(est_lue=False).count(),
    }


@role_required('client')
def dashboard_client(request):
    client = request.user.client_profil
    livraisons_qs = client.livraisons.all()

    stats = {
        'total': livraisons_qs.count(),
        'en_attente': livraisons_qs.filter(statut='en_attente').count(),
        'en_cours': livraisons_qs.filter(statut__in=STATUTS_EN_COURS_CLIENT).count(),
        'livrees': livraisons_qs.filter(statut='livree').count(),
    }

    return render(request, 'client/dashboard.html', {
        **_contexte_client(client, 'dashboard'),
        'stats': stats,
        'livraisons_recentes': livraisons_qs.select_related('livreur__utilisateur')[:5],
    })


@role_required('client')
def mes_livraisons(request):
    client = request.user.client_profil
    q = request.GET.get('q', '').strip()
    filtre = request.GET.get('statut', 'toutes')

    livraisons = client.livraisons.select_related('livreur__utilisateur')

    if filtre in STATUT_FILTRES_CLIENT:
        livraisons = livraisons.filter(statut__in=STATUT_FILTRES_CLIENT[filtre])
    else:
        filtre = 'toutes'

    if q:
        recherche = (
            Q(nom_destinataire__icontains=q)
            | Q(ville_depart__icontains=q)
            | Q(ville_destination__icontains=q)
        )
        if q.isdigit():
            recherche |= Q(pk=int(q))
        livraisons = livraisons.filter(recherche)

    return render(request, 'client/mes_livraisons.html', {
        **_contexte_client(client, 'mes_livraisons'),
        'livraisons': livraisons,
        'filtre': filtre,
        'q': q,
    })


def calculer_prix(type_vehicule):
    return Livraison.prix_pour(type_vehicule)


@role_required('client')
def choisir_vehicule(request):
    client = request.user.client_profil
    return render(request, 'client/choisir_vehicule.html', {
        **_contexte_client(client, 'creer_livraison'),
        'tarifs': Livraison.TARIFS_VEHICULE,
    })


def _erreurs_creation_livraison(donnees, point_relais, date_programmee):
    erreurs = []
    if donnees['type_livraison'] == 'point_relais' and point_relais is None:
        erreurs.append("Veuillez choisir un point relais.")

    obligatoires = {
        'adresse_depart': "l'adresse de départ",
        'ville_depart': "la ville de départ",
        'nom_destinataire': "le nom du destinataire",
        'tel_destinataire': "le téléphone du destinataire",
    }
    if donnees['type_livraison'] == 'domicile':
        obligatoires['adresse_destination'] = "l'adresse de destination"
        obligatoires['ville_destination'] = "la ville de destination"
    for champ, libelle in obligatoires.items():
        if not donnees[champ]:
            erreurs.append(f"Veuillez renseigner {libelle}.")

    if not donnees['livraison_immediate']:
        # Petite marge pour le temps passé à remplir le formulaire.
        minimum = timezone.now() + timedelta(minutes=55)
        if date_programmee is None:
            erreurs.append("Veuillez choisir la date et l'heure de la livraison programmée.")
        elif date_programmee < minimum:
            erreurs.append("Une livraison doit être programmée au moins 1 heure à l'avance.")
    return erreurs


@role_required('client')
def creer_livraison(request, type_vehicule):
    client = request.user.client_profil
    if type_vehicule not in Livraison.TARIFS_VEHICULE:
        messages.error(request, "Type de véhicule inconnu.")
        return redirect('livraison:choisir_vehicule')

    points_relais = PointRelais.objects.filter(est_actif=True)
    donnees = {}

    if request.method == 'POST':
        champs = [
            'type_livraison', 'adresse_depart', 'ville_depart', 'adresse_destination', 'ville_destination',
            'nom_destinataire', 'tel_destinataire', 'description_contenu', 'instructions_speciales',
            'point_relais', 'date_programmee',
        ]
        donnees = {champ: request.POST.get(champ, '').strip() for champ in champs}
        if donnees['type_livraison'] not in dict(Livraison.MODES):
            donnees['type_livraison'] = 'domicile'
        donnees['livraison_immediate'] = request.POST.get('livraison_immediate') != 'false'

        if donnees['type_livraison'] == 'point_relais':
            donnees['instructions_speciales'] = ''  # sans objet pour un retrait en point relais

        # Paiement : seul un colis en point relais peut être payé par le destinataire,
        # et dans ce cas uniquement en espèces au gérant.
        donnees['payeur'] = request.POST.get('payeur', 'client')
        if donnees['type_livraison'] != 'point_relais' or donnees['payeur'] not in dict(Livraison.PAYEUR):
            donnees['payeur'] = 'client'
        donnees['mode_paiement'] = request.POST.get('mode_paiement', 'especes')
        if donnees['payeur'] == 'destinataire' or donnees['mode_paiement'] not in dict(Livraison.MODE_PAIEMENT):
            donnees['mode_paiement'] = 'especes'

        point_relais = None
        if donnees['type_livraison'] == 'point_relais' and donnees['point_relais'].isdigit():
            point_relais = points_relais.filter(pk=donnees['point_relais']).first()
            if point_relais:
                donnees['adresse_destination'] = point_relais.adresse
                donnees['ville_destination'] = point_relais.ville
        date_programmee = None if donnees['livraison_immediate'] else _parser_datetime(donnees['date_programmee'])

        erreurs = _erreurs_creation_livraison(donnees, point_relais, date_programmee)
        for erreur in erreurs:
            messages.error(request, erreur)

        if not erreurs:
            livraison = Livraison.objects.create(
                client=client,
                type_vehicule=type_vehicule,
                type_livraison=donnees['type_livraison'],
                point_relais=point_relais,
                adresse_depart=donnees['adresse_depart'],
                ville_depart=donnees['ville_depart'],
                adresse_destination=donnees['adresse_destination'],
                ville_destination=donnees['ville_destination'],
                nom_destinataire=donnees['nom_destinataire'],
                tel_destinataire=donnees['tel_destinataire'],
                description_contenu=donnees['description_contenu'],
                instructions_speciales=donnees['instructions_speciales'],
                payeur=donnees['payeur'],
                mode_paiement=donnees['mode_paiement'],
                livraison_immediate=donnees['livraison_immediate'],
                date_programmee=date_programmee,
                statut='en_attente',
                montant=calculer_prix(type_vehicule),
            )

            Notification.objects.create(
                destinataire=request.user,
                titre="Demande enregistrée ✅",
                message=f"Votre livraison #{livraison.id} est en attente d'un livreur.",
                canal='app',
            )
            for admin_utilisateur in Utilisateur.objects.filter(role='admin'):
                Notification.objects.create(
                    destinataire=admin_utilisateur,
                    titre="Nouvelle livraison",
                    message=(
                        f"Nouvelle demande #{livraison.id} de {client.utilisateur.prenom}. "
                        f"Véhicule : {livraison.get_type_vehicule_display()}."
                    ),
                    canal='app',
                )

            messages.success(request, f"Livraison #{livraison.id} créée avec succès.")
            return redirect('livraison:detail_livraison', pk=livraison.id)

    date_min = timezone.localtime(timezone.now() + timedelta(hours=1))
    return render(request, 'client/creer_livraison.html', {
        **_contexte_client(client, 'creer_livraison'),
        'type_vehicule': type_vehicule,
        'type_vehicule_libelle': dict(Livraison.TYPE_VEHICULE)[type_vehicule],
        'icone_vehicule': {'moto': '🏍️', 'voiture': '🚗', 'camion': '🚚'}[type_vehicule],
        'points_relais': points_relais,
        'prix': calculer_prix(type_vehicule),
        'donnees': donnees,
        'date_min': date_min.strftime('%Y-%m-%dT%H:%M'),
    })


@role_required('client')
def modifier_livraison(request, pk):
    client = request.user.client_profil
    livraison = get_object_or_404(Livraison, pk=pk, client=client)

    if livraison.statut != 'en_attente':
        messages.error(request, "Cette livraison ne peut plus être modifiée.")
        return redirect('livraison:detail_livraison', pk=pk)

    if request.method == 'POST':
        livraison.adresse_depart = request.POST.get('adresse_depart', '').strip()
        livraison.ville_depart = request.POST.get('ville_depart', '').strip()
        livraison.adresse_destination = request.POST.get('adresse_destination', '').strip()
        livraison.ville_destination = request.POST.get('ville_destination', '').strip()
        livraison.livraison_immediate = request.POST.get('livraison_immediate') != 'false'
        livraison.date_programmee = (
            None if livraison.livraison_immediate
            else _parser_datetime(request.POST.get('date_programmee')) or livraison.date_programmee
        )
        livraison.description_contenu = request.POST.get('description_contenu', '').strip()
        livraison.instructions_speciales = (
            '' if livraison.type_livraison == 'point_relais'
            else request.POST.get('instructions_speciales', '').strip()
        )
        livraison.save()

        for admin_utilisateur in Utilisateur.objects.filter(role='admin'):
            Notification.objects.create(
                destinataire=admin_utilisateur,
                titre=f"Livraison #{livraison.id} modifiée",
                message=f"{client} a modifié les informations de la livraison #{livraison.id}.",
                canal='app',
            )

        messages.success(request, "Livraison modifiée avec succès.")
        return redirect('livraison:detail_livraison', pk=pk)

    return render(request, 'client/modifier_livraison.html', {
        **_contexte_client(client, 'mes_livraisons'),
        'livraison': livraison,
        'date_programmee_input': (
            timezone.localtime(livraison.date_programmee).strftime('%Y-%m-%dT%H:%M')
            if livraison.date_programmee else ''
        ),
    })


@role_required('client')
def notifications_client(request):
    client = request.user.client_profil
    return render(request, 'client/notifications.html', {
        **_contexte_client(client, 'notifications'),
        'notifications': request.user.notifications.all(),
    })


@role_required('client')
def profil_client(request):
    client = request.user.client_profil
    utilisateur = request.user

    if request.method == 'POST':
        if 'maj_infos' in request.POST:
            nom = request.POST.get('nom', '').strip()
            prenom = request.POST.get('prenom', '').strip()
            telephone = request.POST.get('telephone', '').strip()
            if not nom or not prenom:
                messages.error(request, "Le nom et le prénom sont obligatoires.")
            else:
                utilisateur.nom = nom
                utilisateur.prenom = prenom
                utilisateur.telephone = telephone
                utilisateur.save()
                client.adresse = request.POST.get('adresse', '').strip()
                client.ville = request.POST.get('ville', '').strip()
                client.save()
                messages.success(request, "Profil mis à jour avec succès.")

        elif 'maj_mot_de_passe' in request.POST:
            ancien = request.POST.get('ancien_mot_de_passe', '')
            nouveau = request.POST.get('nouveau_mot_de_passe', '')
            confirmation = request.POST.get('confirmation_mot_de_passe', '')
            if not utilisateur.check_password(ancien):
                messages.error(request, "Ancien mot de passe incorrect.")
            elif not nouveau or nouveau != confirmation:
                messages.error(request, "Les nouveaux mots de passe ne correspondent pas.")
            else:
                utilisateur.set_password(nouveau)
                utilisateur.save()
                update_session_auth_hash(request, utilisateur)
                messages.success(request, "Mot de passe modifié avec succès.")

        return redirect('livraison:profil_client')

    return render(request, 'client/profil.html', _contexte_client(client, 'profil'))


@login_required
def detail_livraison(request, pk):
    livraison = get_object_or_404(Livraison, pk=pk)
    if not _peut_voir_livraison(request.user, livraison):
        messages.error(request, "Accès non autorisé à cette livraison.")
        return _redirection_selon_role(request.user)

    contexte = {
        'livraison': livraison,
        'messages_livraison': livraison.messages.all(),
        'evaluation': getattr(livraison, 'evaluation', None),
    }
    if request.user.role == 'admin':
        contexte['trajet'] = [
            [p.latitude, p.longitude]
            for p in livraison.positions.order_by('date_heure')
        ]
    return render(request, 'client/detail_livraison.html', contexte)


@role_required('client')
def annuler_livraison(request, pk):
    livraison = get_object_or_404(Livraison, pk=pk, client=request.user.client_profil)

    if livraison.statut != 'en_attente':
        messages.error(request, "Cette livraison ne peut plus être annulée.")
    elif request.method == 'POST':
        livraison.motif_annulation = request.POST.get('motif_annulation', '').strip()
        livraison.statut = 'annulee'
        livraison.save()

        for admin_utilisateur in Utilisateur.objects.filter(role='admin'):
            Notification.objects.create(
                destinataire=admin_utilisateur,
                titre=f"Livraison #{livraison.id} annulée",
                message=f"{livraison.client} a annulé la livraison #{livraison.id}.",
                canal='app',
            )
        if livraison.livreur:
            Notification.objects.create(
                destinataire=livraison.livreur.utilisateur,
                titre=f"Livraison #{livraison.id} annulée",
                message=f"Le client a annulé la livraison #{livraison.id}.",
                canal='app',
            )

        messages.success(request, "Livraison annulée.")

    return redirect('livraison:detail_livraison', pk=pk)


@role_required('client')
def evaluer_livreur(request, pk):
    livraison = get_object_or_404(Livraison, pk=pk, client=request.user.client_profil)

    if livraison.statut != 'livree':
        messages.error(request, "Vous ne pouvez évaluer qu'une livraison livrée.")
    elif hasattr(livraison, 'evaluation'):
        messages.info(request, "Vous avez déjà évalué cette livraison.")
    elif request.method == 'POST':
        Evaluation.objects.create(
            livraison=livraison,
            client=livraison.client,
            livreur=livraison.livreur,
            note=int(request.POST.get('note', 5)),
            commentaire=request.POST.get('commentaire', '').strip(),
        )
        messages.success(request, "Merci pour votre évaluation.")

    return redirect('livraison:detail_livraison', pk=pk)


# ── ESPACE LIVREUR ──────────────────────────────────────────────────
STATUTS_MISSION_EN_COURS = ['en_cours_ramassage', 'en_cours_livraison']


def _contexte_livreur(livreur, section):
    """Contexte commun à toutes les pages de l'espace livreur (sidebar, header)."""
    return {
        'livreur': livreur,
        'section': section,
        'nb_notifs_non_lues': livreur.utilisateur.notifications.filter(est_lue=False).count(),
    }


@role_required('livreur')
def dashboard_livreur(request):
    livreur = request.user.livreur_profil

    if request.method == 'POST' and 'toggle_disponibilite' in request.POST:
        if not livreur.est_disponible and not livreur.a_un_vehicule_operationnel():
            messages.error(
                request,
                "Vous devez posséder un véhicule personnel ou avoir un véhicule affecté "
                "par un administrateur pour passer disponible."
            )
        else:
            livreur.est_disponible = not livreur.est_disponible
            livreur.save()
        return redirect('livraison:dashboard_livreur')

    toutes_livraisons = livreur.livraisons.select_related('client__utilisateur')
    mission_en_cours = toutes_livraisons.filter(statut__in=STATUTS_MISSION_EN_COURS).first()
    missions_attribuees = toutes_livraisons.filter(statut='attribuee')
    historique_qs = toutes_livraisons.filter(statut__in=['livree', 'annulee']).order_by('-date_demande')

    paginator = Paginator(historique_qs, 6)
    page_obj = paginator.get_page(request.GET.get('page'))

    stats = {
        'total': toutes_livraisons.count(),
        'en_cours': toutes_livraisons.filter(
            statut__in=['attribuee'] + STATUTS_MISSION_EN_COURS
        ).count(),
        'livrees': toutes_livraisons.filter(statut='livree').count(),
    }

    return render(request, 'livreur/dashboard.html', {
        **_contexte_livreur(livreur, 'dashboard'),
        'stats': stats,
        'mission_en_cours': mission_en_cours,
        'missions_attribuees': missions_attribuees,
        'paiements_a_confirmer': _paiements_livreur(livreur).select_related('client__utilisateur').order_by('date_demande'),
        'page_obj': page_obj,
        'statuts_annulables': STATUTS_ANNULABLES_PAR_LIVREUR,
    })


@role_required('livreur')
def vehicule_livreur(request):
    livreur = request.user.livreur_profil
    return render(request, 'livreur/vehicule.html', _contexte_livreur(livreur, 'vehicule'))


@role_required('livreur')
def notifications_livreur(request):
    livreur = request.user.livreur_profil
    return render(request, 'livreur/notifications.html', {
        **_contexte_livreur(livreur, 'notifications'),
        'notifications': livreur.utilisateur.notifications.all(),
    })


@role_required('livreur')
def marquer_notifications_lues(request):
    if request.method == 'POST':
        request.user.notifications.filter(est_lue=False).update(est_lue=True)
        messages.success(request, "Toutes les notifications ont été marquées comme lues.")
    return redirect('livraison:notifications_livreur')


@role_required('livreur')
def profil_livreur(request):
    livreur = request.user.livreur_profil
    utilisateur = request.user

    if request.method == 'POST':
        if 'maj_infos' in request.POST:
            nom = request.POST.get('nom', '').strip()
            prenom = request.POST.get('prenom', '').strip()
            telephone = request.POST.get('telephone', '').strip()
            if not nom or not prenom:
                messages.error(request, "Le nom et le prénom sont obligatoires.")
            else:
                utilisateur.nom = nom
                utilisateur.prenom = prenom
                utilisateur.telephone = telephone
                utilisateur.save()
                messages.success(request, "Profil mis à jour avec succès.")

        elif 'maj_mot_de_passe' in request.POST:
            ancien = request.POST.get('ancien_mot_de_passe', '')
            nouveau = request.POST.get('nouveau_mot_de_passe', '')
            confirmation = request.POST.get('confirmation_mot_de_passe', '')
            if not utilisateur.check_password(ancien):
                messages.error(request, "Ancien mot de passe incorrect.")
            elif not nouveau or nouveau != confirmation:
                messages.error(request, "Les nouveaux mots de passe ne correspondent pas.")
            else:
                utilisateur.set_password(nouveau)
                utilisateur.save()
                update_session_auth_hash(request, utilisateur)
                messages.success(request, "Mot de passe modifié avec succès.")

        return redirect('livraison:profil_livreur')

    return render(request, 'livreur/profil.html', _contexte_livreur(livreur, 'profil'))


@role_required('livreur')
def maj_statut_livraison(request, pk):
    livraison = get_object_or_404(Livraison, pk=pk, livreur=request.user.livreur_profil)

    if request.method == 'POST':
        nouveau_statut = request.POST.get('statut')
        if nouveau_statut == 'probleme' and livraison.statut not in ('livree', 'annulee'):
            livraison.maj_statut('probleme')
            messages.warning(request, "Problème signalé sur la livraison.")
        elif _prochain_statut(livraison) == nouveau_statut:
            livraison.maj_statut(nouveau_statut)
            messages.success(request, "Statut mis à jour.")
        else:
            messages.error(request, "Transition de statut invalide.")

    return redirect(_url_retour(request, reverse('livraison:detail_livraison', args=[pk])))


STATUTS_ANNULABLES_PAR_LIVREUR = ['attribuee', 'en_cours_ramassage']


MOTIFS_ANNULATION_LIVREUR = {
    'panne':       'Panne de véhicule',
    'position':    'Problème de position',
    'urgence':     'Urgence personnelle',
    'arrestation': 'Arrestation par agent de police',
}

RAPPELS_DISPONIBILITE = {
    'urgence': "⚠️ Vous avez annulé pour urgence personnelle. N'oubliez pas de désactiver votre disponibilité.",
    'arrestation': "⚠️ Vous avez signalé une arrestation. N'oubliez pas de désactiver votre disponibilité.",
}


@role_required('livreur')
def annuler_livraison_livreur(request, pk):
    livreur = request.user.livreur_profil
    livraison = get_object_or_404(Livraison, pk=pk, livreur=livreur)

    if request.method == 'POST':
        if livraison.statut not in STATUTS_ANNULABLES_PAR_LIVREUR:
            messages.error(request, "Impossible d'annuler cette livraison.")
            return redirect('livraison:dashboard_livreur')

        motif = request.POST.get('motif', '')
        if motif not in MOTIFS_ANNULATION_LIVREUR:
            messages.error(request, "Veuillez choisir un motif d'annulation valide.")
            return redirect('livraison:dashboard_livreur')

        description = request.POST.get('description', '').strip()
        libelle_motif = MOTIFS_ANNULATION_LIVREUR[motif]

        # Retirer le livreur de la livraison, qui repasse en attente d'attribution.
        livraison.livreur = None
        livraison.statut = 'en_attente'
        livraison.motif_annulation = f"{libelle_motif}: {description}" if description else libelle_motif
        livraison.save()

        vehicule_entreprise_libere = False
        if motif == 'panne':
            # Le livreur reste disponible : seul son véhicule (s'il est celui de l'entreprise) est impacté.
            livreur.est_disponible = True
            livreur.save()
            if not livreur.possede_vehicule_perso:
                vehicule = getattr(livreur, 'vehicule', None)
                if vehicule:
                    vehicule.etat = 'en_maintenance'
                    vehicule.livreur = None
                    vehicule.save()
                    vehicule_entreprise_libere = True

        elif motif == 'position':
            livreur.est_disponible = True
            livreur.save()
            LivraisonExclusion.objects.get_or_create(
                livraison=livraison, livreur=livreur, defaults={'motif': 'position'}
            )

        # Pour 'urgence' et 'arrestation' : le véhicule n'est pas touché et la
        # disponibilité n'est pas modifiée — c'est au livreur de la désactiver lui-même.

        # Notification systématique au client.
        Notification.objects.create(
            destinataire=livraison.client.utilisateur,
            titre=f"Livraison #{livraison.id} annulée",
            message=(
                f"Votre livraison #{livraison.id} a été annulée par le livreur. "
                "Elle sera réattribuée prochainement."
            ),
            canal='app',
        )

        # Notification à l'admin, adaptée au motif.
        if motif == 'arrestation':
            titre_admin = "🚨 URGENT - Livreur arrêté"
            message_admin = (
                f"🚨 URGENT : Livreur {livreur} a été arrêté par un agent de police "
                f"lors de la livraison #{livraison.id}. Veuillez prendre les mesures nécessaires."
            )
        elif motif == 'panne':
            titre_admin = f"🔧 Panne véhicule - Livraison #{livraison.id}"
            if vehicule_entreprise_libere:
                message_admin = (
                    f"Véhicule {vehicule.immatriculation} en panne. Livreur {livreur} sans véhicule. "
                    "Action requise : affecter un autre véhicule."
                )
            else:
                message_admin = f"Livreur {livreur} en panne (véhicule personnel). Possible affectation véhicule temporaire."
        elif motif == 'position':
            titre_admin = f"📍 Problème position - Livraison #{livraison.id}"
            message_admin = f"Livreur {livreur} a annulé la livraison #{livraison.id} pour problème de position."
        else:
            titre_admin = f"⚠️ Urgence personnelle - Livraison #{livraison.id}"
            message_admin = (
                f"Livreur {livreur} a annulé la livraison #{livraison.id} pour urgence personnelle. "
                "Le livreur doit activer son indisponibilité."
            )

        for admin_utilisateur in Utilisateur.objects.filter(role='admin'):
            Notification.objects.create(
                destinataire=admin_utilisateur,
                titre=titre_admin,
                message=message_admin,
                canal='app',
            )

        messages.success(request, "Livraison annulée avec succès. Elle sera réattribuée.")
        if motif in RAPPELS_DISPONIBILITE:
            messages.warning(request, RAPPELS_DISPONIBILITE[motif])

    return redirect('livraison:dashboard_livreur')


# ── ESPACE ADMINISTRATEUR ───────────────────────────────────────────
STATUTS_EN_COURS = ['attribuee', 'en_cours_ramassage', 'en_cours_livraison', 'deposee_relais']

STATUT_FILTRES = {
    'en_attente': ['en_attente'],
    'en_cours':   STATUTS_EN_COURS,
    'livrees':    ['livree'],
    'annulees':   ['annulee', 'probleme', 'non_recupere'],
    'historique': ['livree', 'annulee', 'probleme', 'non_recupere'],
}


def _construire_attribution(livraison, livreurs_dispo):
    """Pour une livraison en attente : la liste des livreurs disponibles avec leur
    compatibilité (véhicule/permis/capacité), et le sous-ensemble réellement compatible.
    Les livreurs exclus de cette livraison (ex : annulation pour position) n'apparaissent pas."""
    exclus_ids = set(
        LivraisonExclusion.objects.filter(livraison=livraison).values_list('livreur_id', flat=True)
    )
    lignes = []
    for livreur in livreurs_dispo:
        if livreur.pk in exclus_ids:
            continue
        vehicule_type, capacite = _vehicule_operationnel(livreur)
        lignes.append({
            'livreur': livreur,
            'vehicule_type': vehicule_type,
            'capacite': capacite,
            'compatible': _raison_incompatibilite(livreur, livraison.type_vehicule) is None,
        })
    compatibles = [ligne['livreur'] for ligne in lignes if ligne['compatible']]
    return {'livraison': livraison, 'lignes': lignes, 'compatibles': compatibles}


@role_required('admin')
def dashboard_admin(request):
    livraisons_qs = Livraison.objects.all()
    stats = {
        'total_livraisons': livraisons_qs.count(),
        'en_attente': livraisons_qs.filter(statut='en_attente').count(),
        'en_cours': livraisons_qs.filter(statut__in=STATUTS_EN_COURS).count(),
        'livrees': livraisons_qs.filter(statut='livree').count(),
        'total_clients': Client.objects.count(),
        'livreurs_actifs': Livreur.objects.filter(utilisateur__est_actif=True).count(),
        'vehicules_disponibles': Vehicule.objects.filter(etat='disponible').count(),
        'total_points_relais': PointRelais.objects.count(),
        'points_relais_actifs': PointRelais.objects.filter(est_actif=True).count(),
        'colis_en_relais': livraisons_qs.filter(statut='deposee_relais').count(),
    }

    paiements_dus = livraisons_qs.filter(
        statut__in=STATUTS_LIVRAISON_PAIEMENT_DU, statut_paiement__in=STATUTS_PAIEMENT_DUS
    )
    stats_paiements = {
        'encaisse_aujourdhui': livraisons_qs.filter(
            statut_paiement__in=Livraison.STATUTS_PAYES, date_paiement__date=timezone.localdate()
        ).aggregate(total=Sum('montant'))['total'] or 0,
        'en_attente': paiements_dus.count(),
        'especes': livraisons_qs.filter(statut_paiement='recu_especes').count(),
        'mobile': livraisons_qs.filter(statut_paiement='confirme_mobile').count(),
    }

    livreurs_dispo_attribution = Livreur.objects.filter(
        utilisateur__est_actif=True, est_disponible=True
    ).select_related('utilisateur', 'vehicule')
    en_attente_attribution = Livraison.objects.filter(statut='en_attente').select_related(
        'client__utilisateur'
    )[:8]
    attributions = [_construire_attribution(l, livreurs_dispo_attribution) for l in en_attente_attribution]

    return render(request, 'admin/dashboard.html', {
        'stats': stats,
        'stats_paiements': stats_paiements,
        'paiements_en_attente': paiements_dus.select_related(
            'client__utilisateur', 'livreur__utilisateur', 'point_relais'
        ).order_by('date_demande')[:15],
        'attributions': attributions,
        'section': 'dashboard',
    })


@role_required('admin')
def liste_livraisons_admin(request):
    q = request.GET.get('q', '').strip()
    filtre = request.GET.get('statut', 'toutes')

    livraisons = Livraison.objects.select_related('client__utilisateur', 'livreur__utilisateur')

    if filtre in STATUT_FILTRES:
        livraisons = livraisons.filter(statut__in=STATUT_FILTRES[filtre])
    else:
        filtre = 'toutes'

    if q:
        recherche = (
            Q(nom_destinataire__icontains=q)
            | Q(client__utilisateur__nom__icontains=q)
            | Q(client__utilisateur__prenom__icontains=q)
            | Q(client__utilisateur__email__icontains=q)
        )
        if q.isdigit():
            recherche |= Q(pk=int(q))
        livraisons = livraisons.filter(recherche)

    paginator = Paginator(livraisons, 10)
    page_obj = paginator.get_page(request.GET.get('page'))

    livreurs_dispo = Livreur.objects.filter(
        utilisateur__est_actif=True, est_disponible=True
    ).select_related('utilisateur', 'vehicule')
    attributions = {
        livraison.id: _construire_attribution(livraison, livreurs_dispo)
        for livraison in page_obj if livraison.statut == 'en_attente'
    }

    return render(request, 'admin/livraisons.html', {
        'page_obj': page_obj,
        'attributions': attributions,
        'filtre': filtre,
        'q': q,
        'section': 'livraisons',
    })


@role_required('admin')
def annuler_livraison_admin(request, pk):
    livraison = get_object_or_404(Livraison, pk=pk)

    if request.method == 'POST':
        if livraison.statut in ('livree', 'annulee'):
            messages.error(request, "Cette livraison ne peut plus être annulée.")
        else:
            livraison.motif_annulation = request.POST.get('motif_annulation', '').strip()
            livraison.statut = 'annulee'
            livraison.save()
            messages.success(request, f"Livraison #{livraison.id} annulée.")

    return redirect(_url_retour(request, 'livraison:liste_livraisons_admin'))


@role_required('admin')
def liste_livreurs_admin(request):
    onglet = request.GET.get('onglet', 'tous')
    livreurs = Livreur.objects.select_related('utilisateur', 'vehicule')

    if onglet == 'actifs':
        livreurs = livreurs.filter(utilisateur__est_actif=True)
    elif onglet == 'validation':
        livreurs = livreurs.filter(utilisateur__est_actif=False)
    else:
        onglet = 'tous'

    return render(request, 'admin/livreurs.html', {
        'livreurs': livreurs,
        'onglet': onglet,
        'nb_validation': Livreur.objects.filter(utilisateur__est_actif=False).count(),
        'section': 'livreurs',
    })


@role_required('admin')
def attribuer_livreur(request, pk):
    livraison = get_object_or_404(Livraison, pk=pk)

    if request.method == 'POST':
        livreur = Livreur.objects.filter(
            pk=request.POST.get('livreur_id'),
            utilisateur__est_actif=True, est_disponible=True,
        ).first()

        if livreur is None:
            messages.error(request, "Ce livreur n'est pas éligible : il doit être actif et disponible.")
        elif LivraisonExclusion.objects.filter(livraison=livraison, livreur=livreur).exists():
            messages.error(request, f"{livreur} a déjà annulé cette livraison et ne peut pas s'en voir réattribuer.")
        else:
            raison = _raison_incompatibilite(livreur, livraison.type_vehicule)
            if raison:
                messages.error(request, raison)
            else:
                livraison.livreur = livreur
                livraison.statut = 'attribuee'
                livraison.save()
                Notification.objects.create(
                    destinataire=livreur.utilisateur,
                    titre=f"Nouvelle livraison #{livraison.id}",
                    message="Une livraison vous a été attribuée.",
                )
                messages.success(request, f"Livraison #{livraison.id} attribuée à {livreur}.")

    return redirect('livraison:dashboard_admin')


@role_required('admin')
def valider_livreur(request, user_id):
    livreur = get_object_or_404(Livreur, pk=user_id)

    if request.method == 'POST':
        livreur.utilisateur.est_actif = True
        livreur.utilisateur.save()
        Notification.objects.create(
            destinataire=livreur.utilisateur,
            titre="Compte validé",
            message="Votre compte livreur a été validé, vous pouvez maintenant vous connecter.",
        )
        messages.success(request, f"{livreur} a été validé.")

    return redirect(_url_retour(request, 'livraison:liste_livreurs_admin'))


# ── GESTION DES VÉHICULES ────────────────────────────────────────────
@role_required('admin')
def liste_vehicules_admin(request):
    onglet = request.GET.get('onglet', 'tous')
    if onglet not in ('parking', 'affectes', 'maintenance'):
        onglet = 'tous'
    vehicules = _vehicules_filtres(onglet, '')

    # Un livreur peut recevoir un véhicule s'il est actif, n'en a pas déjà un, et détient
    # le permis correspondant au type de véhicule — indépendamment de sa disponibilité.
    livreurs_actifs_sans_vehicule = Livreur.objects.filter(
        utilisateur__est_actif=True, vehicule__isnull=True
    )

    return render(request, 'admin/vehicules.html', {
        'vehicules': vehicules,
        'onglet': onglet,
        'compte_tous': Vehicule.objects.count(),
        'compte_parking': Vehicule.objects.filter(etat='disponible').count(),
        'compte_affectes': Vehicule.objects.filter(etat='affecte').count(),
        'compte_maintenance': Vehicule.objects.filter(etat='en_maintenance').count(),
        'livreurs_actifs_sans_vehicule': livreurs_actifs_sans_vehicule,
        'livreurs_moto': livreurs_actifs_sans_vehicule.filter(type_permis__in=PERMIS_REQUIS['moto']),
        'livreurs_voiture': livreurs_actifs_sans_vehicule.filter(type_permis__in=PERMIS_REQUIS['voiture']),
        'section': 'vehicules',
    })


@role_required('admin')
def ajouter_vehicule(request):
    if request.method == 'POST':
        immatriculation = request.POST.get('immatriculation', '').strip()
        if not immatriculation:
            messages.error(request, "L'immatriculation est obligatoire.")
        elif Vehicule.objects.filter(immatriculation=immatriculation).exists():
            messages.error(request, "Cette immatriculation existe déjà.")
        else:
            Vehicule.objects.create(
                immatriculation=immatriculation,
                type_vehicule=request.POST.get('type_vehicule'),
                marque=request.POST.get('marque', '').strip(),
                modele=request.POST.get('modele', '').strip(),
                couleur=request.POST.get('couleur', '').strip(),
                capacite_charge=request.POST.get('capacite_charge') or 0,
                photo=request.FILES.get('photo'),
            )
            messages.success(request, "Véhicule ajouté avec succès.")

    return redirect('livraison:liste_vehicules_admin')


@role_required('admin')
def modifier_vehicule(request, pk):
    vehicule = get_object_or_404(Vehicule, pk=pk)

    if request.method == 'POST':
        immatriculation = request.POST.get('immatriculation', '').strip()
        if not immatriculation:
            messages.error(request, "L'immatriculation est obligatoire.")
        elif Vehicule.objects.filter(immatriculation=immatriculation).exclude(pk=pk).exists():
            messages.error(request, "Cette immatriculation est déjà utilisée par un autre véhicule.")
        else:
            vehicule.immatriculation = immatriculation
            vehicule.type_vehicule = request.POST.get('type_vehicule')
            vehicule.marque = request.POST.get('marque', '').strip()
            vehicule.modele = request.POST.get('modele', '').strip()
            vehicule.couleur = request.POST.get('couleur', '').strip()
            vehicule.capacite_charge = request.POST.get('capacite_charge') or 0
            if request.FILES.get('photo'):
                vehicule.photo = request.FILES['photo']
            vehicule.save()
            messages.success(request, "Véhicule modifié avec succès.")

    return redirect('livraison:liste_vehicules_admin')


@role_required('admin')
def supprimer_vehicule(request, pk):
    vehicule = get_object_or_404(Vehicule, pk=pk)

    if request.method == 'POST':
        vehicule.delete()
        messages.success(request, "Véhicule supprimé.")

    return redirect('livraison:liste_vehicules_admin')


@role_required('admin')
def detail_vehicule(request, pk):
    vehicule = get_object_or_404(Vehicule, pk=pk)
    return render(request, 'admin/detail_vehicule.html', {'vehicule': vehicule})


def _vehicules_filtres(onglet, q):
    vehicules = Vehicule.objects.select_related('livreur__utilisateur')

    if onglet == 'parking':
        vehicules = vehicules.filter(etat='disponible')
    elif onglet == 'affectes':
        vehicules = vehicules.filter(etat='affecte')
    elif onglet == 'maintenance':
        vehicules = vehicules.filter(etat='en_maintenance')

    if q:
        vehicules = vehicules.filter(
            Q(immatriculation__icontains=q)
            | Q(marque__icontains=q)
            | Q(modele__icontains=q)
            | Q(couleur__icontains=q)
            | Q(type_vehicule__icontains=q)
            | Q(livreur__utilisateur__nom__icontains=q)
            | Q(livreur__utilisateur__prenom__icontains=q)
        )
    return vehicules


@role_required('admin')
def recherche_vehicules(request):
    """Filtre les véhicules (onglet + recherche texte) et renvoie uniquement les
    lignes du tableau, pour une recherche en temps réel sans rechargement de page."""
    onglet = request.GET.get('onglet', 'tous')
    q = request.GET.get('q', '').strip()
    vehicules = _vehicules_filtres(onglet, q)

    livreurs_actifs_sans_vehicule = Livreur.objects.filter(
        utilisateur__est_actif=True, vehicule__isnull=True
    )

    return render(request, 'admin/_lignes_vehicules.html', {
        'vehicules': vehicules,
        'livreurs_moto': livreurs_actifs_sans_vehicule.filter(type_permis__in=PERMIS_REQUIS['moto']),
        'livreurs_voiture': livreurs_actifs_sans_vehicule.filter(type_permis__in=PERMIS_REQUIS['voiture']),
    })


@role_required('admin')
def affecter_vehicule(request):
    if request.method != 'POST':
        messages.error(request, "Requête invalide : l'affectation doit être soumise via un formulaire POST.")
        return redirect('livraison:liste_vehicules_admin')

    print("=== affecter_vehicule — données POST reçues ===", flush=True)
    print(dict(request.POST), flush=True)

    vehicule_id = request.POST.get('vehicule_id')
    livreur_id = request.POST.get('livreur_id')

    if not vehicule_id or not livreur_id:
        print(f"Paramètres manquants : vehicule_id={vehicule_id!r}, livreur_id={livreur_id!r}", flush=True)
        messages.error(request, "Veuillez choisir un véhicule et un livreur avant de valider.")
        return redirect('livraison:liste_vehicules_admin')

    try:
        vehicule = Vehicule.objects.get(pk=vehicule_id)
        livreur = Livreur.objects.get(pk=livreur_id)

        if vehicule.etat != 'disponible':
            messages.error(request, f"Le véhicule {vehicule.immatriculation} n'est pas disponible.")
        elif not livreur.utilisateur.est_actif:
            messages.error(request, f"{livreur} n'a pas de compte actif.")
        elif hasattr(livreur, 'vehicule'):
            messages.error(request, f"{livreur} possède déjà un véhicule.")
        elif not _permis_compatible(vehicule.type_vehicule, livreur.type_permis):
            print(
                f"Permis incompatible : véhicule={vehicule.type_vehicule}, "
                f"livreur permis={livreur.type_permis}",
                flush=True,
            )
            messages.error(request, "Ce livreur n'a pas le permis requis pour ce véhicule")
        else:
            vehicule.livreur = livreur
            vehicule.etat = 'affecte'
            vehicule.save()
            Notification.objects.create(
                destinataire=livreur.utilisateur,
                titre="Véhicule affecté",
                message=f"Le véhicule {vehicule.get_type_vehicule_display()} ({vehicule.immatriculation}) vous a été affecté.",
            )
            messages.success(request, f"Véhicule {vehicule.immatriculation} affecté à {livreur}.")

    except Vehicule.DoesNotExist:
        print(f"Véhicule introuvable pour id={vehicule_id!r}", flush=True)
        messages.error(request, "Ce véhicule n'existe pas ou plus.")
    except Livreur.DoesNotExist:
        print(f"Livreur introuvable pour id={livreur_id!r}", flush=True)
        messages.error(request, "Ce livreur n'existe pas ou plus.")
    except (ValueError, TypeError) as exc:
        print(f"Identifiant invalide : {exc}", flush=True)
        messages.error(request, "Identifiant de véhicule ou de livreur invalide.")
    except Exception as exc:
        print(f"Erreur inattendue dans affecter_vehicule : {exc}", flush=True)
        messages.error(request, f"Une erreur est survenue lors de l'affectation : {exc}")

    return redirect('livraison:liste_vehicules_admin')


@role_required('admin')
def liberer_vehicule(request, vehicule_id):
    vehicule = get_object_or_404(Vehicule, pk=vehicule_id)

    if request.method == 'POST':
        livreur = vehicule.livreur
        vehicule.livreur = None
        vehicule.etat = 'disponible'
        vehicule.save()
        if livreur:
            Notification.objects.create(
                destinataire=livreur.utilisateur,
                titre="Véhicule libéré",
                message=f"Le véhicule {vehicule.get_type_vehicule_display()} ({vehicule.immatriculation}) ne vous est plus affecté.",
            )
        messages.success(request, f"Véhicule {vehicule.immatriculation} libéré.")

    return redirect('livraison:liste_vehicules_admin')


@role_required('admin')
def mettre_en_maintenance(request, vehicule_id):
    vehicule = get_object_or_404(Vehicule, pk=vehicule_id)

    if request.method == 'POST':
        if vehicule.livreur:
            messages.error(
                request,
                f"Libérez d'abord le véhicule {vehicule.immatriculation} avant de le mettre en maintenance."
            )
        else:
            vehicule.etat = 'en_maintenance'
            vehicule.save()
            messages.success(request, f"Véhicule {vehicule.immatriculation} placé en maintenance.")

    return redirect('livraison:liste_vehicules_admin')


@role_required('admin')
def marquer_disponible(request, vehicule_id):
    vehicule = get_object_or_404(Vehicule, pk=vehicule_id)

    if request.method == 'POST':
        vehicule.etat = 'disponible'
        vehicule.save()
        messages.success(request, f"Véhicule {vehicule.immatriculation} marqué disponible.")

    return redirect('livraison:liste_vehicules_admin')


# ── GESTION DES CLIENTS ─────────────────────────────────────────────
@role_required('admin')
def liste_clients_admin(request):
    q = request.GET.get('q', '').strip()
    clients = Client.objects.select_related('utilisateur').annotate(nb_livraisons=Count('livraisons'))

    if q:
        clients = clients.filter(
            Q(utilisateur__nom__icontains=q)
            | Q(utilisateur__prenom__icontains=q)
            | Q(utilisateur__email__icontains=q)
            | Q(ville__icontains=q)
        )

    return render(request, 'admin/clients.html', {
        'clients': clients,
        'q': q,
        'section': 'clients',
    })


@role_required('admin')
def parametres_admin(request):
    return render(request, 'admin/parametres.html', {'section': 'parametres'})


# ── GESTION DES POINTS RELAIS ───────────────────────────────────────
def _jours_restants(livraison, aujourd_hui):
    """Jours calendaires avant la date limite de récupération (négatif = expiré)."""
    if not livraison.date_limite_recuperation:
        return None
    return (timezone.localtime(livraison.date_limite_recuperation).date() - aujourd_hui).days


def _urgence(jours_restants):
    """ok (>= 3 j) · alerte (2 j, jour 5) · critique (<= 1 j, jour 7) · expire (< 0)."""
    if jours_restants is None or jours_restants >= 3:
        return 'ok'
    if jours_restants < 0:
        return 'expire'
    if jours_restants <= 1:
        return 'critique'
    return 'alerte'


def _donnees_point_relais(request):
    return {
        'nom': request.POST.get('nom', '').strip(),
        'adresse': request.POST.get('adresse', '').strip(),
        'ville': request.POST.get('ville', '').strip(),
        'telephone': request.POST.get('telephone', '').strip(),
    }


def _erreur_point_relais(donnees):
    if not (donnees['nom'] and donnees['adresse'] and donnees['ville']):
        return "Le nom, l'adresse et la ville sont obligatoires."
    return None


@role_required('admin')
def liste_points_relais(request):
    q = request.GET.get('q', '').strip()
    points_relais = PointRelais.objects.annotate(
        nb_gerants=Count('gerants', distinct=True),
        nb_colis=Count('livraisons', filter=Q(livraisons__statut='deposee_relais'), distinct=True),
    ).order_by('-est_actif', 'ville', 'nom')

    if q:
        points_relais = points_relais.filter(
            Q(nom__icontains=q) | Q(ville__icontains=q) | Q(adresse__icontains=q)
        )

    return render(request, 'admin/points_relais/liste.html', {
        'points_relais': points_relais,
        'q': q,
        'section': 'points_relais',
    })


@role_required('admin')
def ajouter_point_relais(request):
    donnees = {'nom': '', 'adresse': '', 'ville': '', 'telephone': ''}
    if request.method == 'POST':
        donnees = _donnees_point_relais(request)
        erreur = _erreur_point_relais(donnees)
        if erreur:
            messages.error(request, erreur)
        else:
            PointRelais.objects.create(**donnees)
            messages.success(request, "Point relais ajouté avec succès !")
            return redirect('livraison:liste_points_relais')

    return render(request, 'admin/points_relais/ajouter.html', {
        'donnees': donnees,
        'section': 'points_relais',
    })


@role_required('admin')
def detail_point_relais(request, pk):
    point_relais = get_object_or_404(PointRelais, pk=pk)
    aujourd_hui = timezone.localdate()

    gerants = point_relais.gerants.select_related('gerant').order_by('date_affectation')
    gerants_disponibles = Utilisateur.objects.filter(
        role='agent_relais', est_actif=True
    ).exclude(points_relais_geres__point_relais=point_relais).order_by('nom', 'prenom')

    colis_en_attente = list(
        Livraison.objects.filter(point_relais=point_relais, statut='deposee_relais')
        .select_related('client__utilisateur').order_by('date_limite_recuperation')
    )
    colis_expires = []
    for colis in colis_en_attente:
        colis.jours_restants = _jours_restants(colis, aujourd_hui)
        colis.urgence = _urgence(colis.jours_restants)
        if colis.urgence == 'expire':
            colis_expires.append(colis)

    historique = (
        Livraison.objects.filter(
            point_relais=point_relais, date_depot_relais__isnull=False,
            statut__in=['livree', 'non_recupere'],
        ).select_related('client__utilisateur').order_by('-date_depot_relais')[:20]
    )

    return render(request, 'admin/points_relais/detail.html', {
        'point_relais': point_relais,
        'gerants': gerants,
        'gerants_disponibles': gerants_disponibles,
        'colis_en_attente': colis_en_attente,
        'colis_expires': colis_expires,
        'historique': historique,
        'horaires': PointRelais.HORAIRES,
        'section': 'points_relais',
    })


@role_required('admin')
def modifier_point_relais(request, pk):
    point_relais = get_object_or_404(PointRelais, pk=pk)
    donnees = {
        'nom': point_relais.nom, 'adresse': point_relais.adresse,
        'ville': point_relais.ville, 'telephone': point_relais.telephone,
    }
    if request.method == 'POST':
        donnees = _donnees_point_relais(request)
        erreur = _erreur_point_relais(donnees)
        if erreur:
            messages.error(request, erreur)
        else:
            for champ, valeur in donnees.items():
                setattr(point_relais, champ, valeur)
            point_relais.save()
            messages.success(request, "Point relais modifié avec succès !")
            return redirect('livraison:detail_point_relais', pk=pk)

    return render(request, 'admin/points_relais/modifier.html', {
        'point_relais': point_relais,
        'donnees': donnees,
        'section': 'points_relais',
    })


@role_required('admin')
def activer_point_relais(request, pk):
    point_relais = get_object_or_404(PointRelais, pk=pk)
    if request.method == 'POST':
        point_relais.est_actif = True
        point_relais.save()
        messages.success(request, "Point relais activé.")
    return redirect(_url_retour(request, 'livraison:liste_points_relais'))


@role_required('admin')
def desactiver_point_relais(request, pk):
    point_relais = get_object_or_404(PointRelais, pk=pk)
    if request.method == 'POST':
        point_relais.est_actif = False
        point_relais.save()
        messages.success(request, "Point relais désactivé.")
    return redirect(_url_retour(request, 'livraison:liste_points_relais'))


@role_required('admin')
def supprimer_point_relais(request, pk):
    point_relais = get_object_or_404(PointRelais, pk=pk)
    if request.method != 'POST':
        return redirect('livraison:detail_point_relais', pk=pk)

    colis_en_attente = Livraison.objects.filter(point_relais=point_relais, statut='deposee_relais').count()
    if colis_en_attente > 0:
        messages.error(request, f"Impossible de supprimer : {colis_en_attente} colis en attente.")
        return redirect('livraison:detail_point_relais', pk=pk)

    point_relais.delete()
    messages.success(request, "Point relais supprimé.")
    return redirect('livraison:liste_points_relais')


@role_required('admin')
def affecter_gerant(request, pk):
    point_relais = get_object_or_404(PointRelais, pk=pk)
    if request.method == 'POST':
        gerant_id = request.POST.get('gerant_id', '')
        if not gerant_id.isdigit():
            messages.error(request, "Veuillez choisir un gérant.")
            return redirect('livraison:detail_point_relais', pk=pk)
        gerant = get_object_or_404(Utilisateur, pk=gerant_id, role='agent_relais')
        _, cree = GerantPointRelais.objects.get_or_create(point_relais=point_relais, gerant=gerant)
        if cree:
            Notification.objects.create(
                destinataire=gerant,
                titre="Affectation point relais",
                message=f"Vous avez été affecté au point relais {point_relais.nom}.",
                canal='app',
            )
            messages.success(request, "Gérant affecté avec succès !")
        else:
            messages.info(request, f"{gerant} gère déjà ce point relais.")
    return redirect('livraison:detail_point_relais', pk=pk)


@role_required('admin')
def retirer_gerant(request, pk, gerant_id):
    point_relais = get_object_or_404(PointRelais, pk=pk)
    if request.method == 'POST':
        GerantPointRelais.objects.filter(point_relais=point_relais, gerant_id=gerant_id).delete()
        messages.success(request, "Gérant retiré avec succès !")
    return redirect('livraison:detail_point_relais', pk=pk)


@role_required('admin')
def marquer_colis_recupere_admin(request, pk, livraison_id):
    livraison = get_object_or_404(Livraison, pk=livraison_id, point_relais_id=pk, statut='deposee_relais')
    if request.method == 'POST':
        livraison.maj_statut('livree')
        messages.success(request, f"Colis #{livraison.id} marqué comme récupéré.")
    return redirect('livraison:detail_point_relais', pk=pk)


# ── GESTION DES GÉRANTS DE POINTS RELAIS ────────────────────────────
COULEURS_AVATAR = ['#1a237e', '#0d6efd', '#6f42c1', '#198754', '#d63384', '#fd7e14', '#0aa2c0', '#b45309']


def _couleur_avatar(utilisateur):
    return COULEURS_AVATAR[utilisateur.pk % len(COULEURS_AVATAR)]


def _donnees_gerant(request):
    return {
        'prenom': request.POST.get('prenom', '').strip(),
        'nom': request.POST.get('nom', '').strip(),
        'email': request.POST.get('email', '').strip().lower(),
        'telephone': request.POST.get('telephone', '').strip(),
    }


@role_required('admin')
def liste_gerants(request):
    q = request.GET.get('q', '').strip()
    gerants = Utilisateur.objects.filter(role='agent_relais').annotate(
        nb_points=Count('points_relais_geres')
    ).order_by('nom', 'prenom')

    if q:
        gerants = gerants.filter(
            Q(nom__icontains=q) | Q(prenom__icontains=q)
            | Q(email__icontains=q) | Q(telephone__icontains=q)
        )

    gerants = list(gerants)
    for gerant in gerants:
        gerant.couleur_avatar = _couleur_avatar(gerant)

    return render(request, 'admin/gerants/liste.html', {
        'gerants': gerants,
        'q': q,
        'section': 'gerants',
        'sous_section': 'liste',
    })


@role_required('admin')
def ajouter_gerant(request):
    donnees = {'prenom': '', 'nom': '', 'email': '', 'telephone': ''}

    if request.method == 'POST':
        donnees = _donnees_gerant(request)
        if not all(donnees.values()):
            messages.error(request, "Tous les champs sont obligatoires.")
        elif Utilisateur.objects.filter(email=donnees['email']).exists():
            messages.error(request, "Cet email est déjà utilisé.")
        else:
            # Mot de passe provisoire = email (à changer à la première connexion)
            gerant = Utilisateur.objects.create_user(
                password=donnees['email'], role='agent_relais', est_actif=True, **donnees
            )
            Notification.objects.create(
                destinataire=gerant,
                titre="Bienvenue sur LivraisonExpress ! 🎉",
                message=(
                    f"Bonjour {gerant.prenom}, votre compte gérant a été créé par l'administrateur. "
                    f"Vos identifiants : Email : {gerant.email} / Mot de passe provisoire : votre adresse email. "
                    "Veuillez changer votre mot de passe dès votre première connexion."
                ),
                canal='app',
            )
            messages.success(request, f"✅ Compte gérant de {gerant.prenom} {gerant.nom} créé avec succès !")
            return redirect('livraison:detail_gerant', pk=gerant.pk)

    return render(request, 'admin/gerants/ajouter.html', {
        'donnees': donnees,
        'section': 'gerants',
        'sous_section': 'ajouter',
    })


@role_required('admin')
def detail_gerant(request, pk):
    gerant = get_object_or_404(Utilisateur, pk=pk, role='agent_relais')
    gerant.couleur_avatar = _couleur_avatar(gerant)

    points_relais_geres = (
        GerantPointRelais.objects.filter(gerant=gerant)
        .select_related('point_relais')
        .annotate(nb_colis=Count(
            'point_relais__livraisons',
            filter=Q(point_relais__livraisons__statut='deposee_relais'),
        ))
        .order_by('point_relais__nom')
    )
    points_relais_disponibles = PointRelais.objects.filter(est_actif=True).exclude(
        gerants__gerant=gerant
    ).order_by('ville', 'nom')

    return render(request, 'admin/gerants/detail.html', {
        'gerant': gerant,
        'points_relais_geres': points_relais_geres,
        'points_relais_disponibles': points_relais_disponibles,
        'section': 'gerants',
    })


@role_required('admin')
def modifier_gerant(request, pk):
    gerant = get_object_or_404(Utilisateur, pk=pk, role='agent_relais')
    donnees = {'prenom': gerant.prenom, 'nom': gerant.nom, 'email': gerant.email, 'telephone': gerant.telephone}

    if request.method == 'POST':
        donnees = {**_donnees_gerant(request), 'email': gerant.email}  # l'email n'est pas modifiable
        if not all(donnees.values()):
            messages.error(request, "Tous les champs sont obligatoires.")
        else:
            gerant.prenom = donnees['prenom']
            gerant.nom = donnees['nom']
            gerant.telephone = donnees['telephone']
            gerant.save()
            messages.success(request, "✅ Gérant modifié avec succès !")
            return redirect('livraison:detail_gerant', pk=pk)

    return render(request, 'admin/gerants/modifier.html', {
        'gerant': gerant,
        'donnees': donnees,
        'section': 'gerants',
    })


@role_required('admin')
def activer_desactiver_gerant(request, pk):
    gerant = get_object_or_404(Utilisateur, pk=pk, role='agent_relais')
    if request.method == 'POST':
        gerant.est_actif = not gerant.est_actif
        gerant.save()
        statut = "activé" if gerant.est_actif else "désactivé"
        messages.success(request, f"Gérant {statut} avec succès !")
    return redirect(_url_retour(request, 'livraison:liste_gerants'))


@role_required('admin')
def supprimer_gerant(request, pk):
    gerant = get_object_or_404(Utilisateur, pk=pk, role='agent_relais')
    if request.method != 'POST':
        return redirect('livraison:detail_gerant', pk=pk)

    nb_points = GerantPointRelais.objects.filter(gerant=gerant).count()
    if nb_points > 0:
        messages.error(
            request,
            f"Impossible : ce gérant est affecté à {nb_points} point(s) relais. Retirez-le d'abord."
        )
        return redirect('livraison:detail_gerant', pk=pk)

    nom_complet = f"{gerant.prenom} {gerant.nom}"
    gerant.delete()
    messages.success(request, f"Gérant {nom_complet} supprimé.")
    return redirect('livraison:liste_gerants')


@role_required('admin')
def affecter_gerant_point_relais(request, pk):
    gerant = get_object_or_404(Utilisateur, pk=pk, role='agent_relais')
    if request.method == 'POST':
        point_relais_id = request.POST.get('point_relais_id', '')
        if not point_relais_id.isdigit():
            messages.error(request, "Veuillez choisir un point relais.")
            return redirect('livraison:detail_gerant', pk=pk)
        point_relais = get_object_or_404(PointRelais, pk=point_relais_id)
        _, cree = GerantPointRelais.objects.get_or_create(point_relais=point_relais, gerant=gerant)
        if cree:
            Notification.objects.create(
                destinataire=gerant,
                titre="Nouvelle affectation 📍",
                message=(
                    f"Vous avez été affecté au point relais {point_relais.nom} — {point_relais.adresse}. "
                    f"Horaires : {PointRelais.HORAIRES}."
                ),
                canal='app',
            )
            messages.success(request, f"✅ {gerant.prenom} affecté à {point_relais.nom} !")
        else:
            messages.info(request, f"{gerant.prenom} gère déjà {point_relais.nom}.")
    return redirect('livraison:detail_gerant', pk=pk)


@role_required('admin')
def retirer_gerant_point_relais(request, pk, relais_id):
    gerant = get_object_or_404(Utilisateur, pk=pk, role='agent_relais')
    point_relais = get_object_or_404(PointRelais, pk=relais_id)
    if request.method == 'POST':
        GerantPointRelais.objects.filter(point_relais=point_relais, gerant=gerant).delete()
        messages.success(request, f"Gérant retiré de {point_relais.nom}.")
    return redirect('livraison:detail_gerant', pk=pk)


# ── ESPACE GÉRANT DE POINT RELAIS ───────────────────────────────────
def _contexte_gerant(request, section):
    """Contexte commun à toutes les pages de l'espace gérant (sidebar, header, bandeau)."""
    return {
        'section': section,
        'nb_notifs_non_lues': request.user.notifications.filter(est_lue=False).count(),
        'mdp_provisoire': request.session.get('mdp_provisoire', False),
    }


def _colis_du_gerant(gerant):
    """Colis stockés dans les points relais que gère ce gérant."""
    return Livraison.objects.filter(
        point_relais__gerants__gerant=gerant, statut='deposee_relais'
    ).distinct()


@role_required('agent_relais')
def dashboard_gerant(request):
    gerant = request.user
    aujourd_hui = timezone.localdate()

    points_relais = (
        GerantPointRelais.objects.filter(gerant=gerant)
        .select_related('point_relais')
        .annotate(nb_colis=Count(
            'point_relais__livraisons',
            filter=Q(point_relais__livraisons__statut='deposee_relais'),
        ))
        .order_by('point_relais__nom')
    )

    colis_en_attente = list(
        _colis_du_gerant(gerant)
        .select_related('client__utilisateur', 'point_relais')
        .order_by('date_limite_recuperation')
    )
    for colis in colis_en_attente:
        colis.jours_restants = _jours_restants(colis, aujourd_hui)
        colis.urgence = _urgence(colis.jours_restants)

    return render(request, 'gerant/dashboard.html', {
        **_contexte_gerant(request, 'dashboard'),
        'gerant': gerant,
        'points_relais': points_relais,
        'colis_en_attente': colis_en_attente,
        'total_colis': len(colis_en_attente),
        'colis_urgents': sum(1 for c in colis_en_attente if c.urgence in ('alerte', 'critique')),
        'colis_expires': sum(1 for c in colis_en_attente if c.urgence == 'expire'),
        'horaires': PointRelais.HORAIRES,
    })


@role_required('agent_relais')
def marquer_colis_recupere(request, pk):
    if request.method == 'POST':
        livraison = get_object_or_404(_colis_du_gerant(request.user), pk=pk)
        if livraison.payeur == 'destinataire' and not livraison.est_payee:
            messages.error(request, "Confirmez d'abord le paiement du destinataire avant de lui remettre le colis.")
            return redirect('livraison:dashboard_gerant')
        # maj_statut renseigne date_livraison, crée la facture et notifie le client
        livraison.maj_statut('livree')
        messages.success(request, "✅ Colis marqué comme récupéré !")
    return redirect('livraison:dashboard_gerant')


@role_required('agent_relais')
def signaler_colis_non_recupere(request, pk):
    if request.method == 'POST':
        livraison = get_object_or_404(
            _colis_du_gerant(request.user).select_related('point_relais', 'client__utilisateur'), pk=pk
        )
        if _urgence(_jours_restants(livraison, timezone.localdate())) != 'expire':
            messages.error(request, "Ce colis est encore dans son délai de récupération.")
            return redirect('livraison:dashboard_gerant')

        livraison.statut = 'non_recupere'
        livraison.save(update_fields=['statut'])
        relais = livraison.point_relais.nom
        for admin in Utilisateur.objects.filter(role='admin'):
            Notification.objects.create(
                destinataire=admin,
                titre=f"🚨 Colis #{livraison.id} non récupéré",
                message=(
                    f"Le colis #{livraison.id} n'a pas été récupéré au point relais {relais} "
                    f"(signalé par {request.user}). Action requise."
                ),
                canal='app',
            )
        Notification.objects.create(
            destinataire=livraison.client.utilisateur,
            titre=f"❌ Colis #{livraison.id} non récupéré",
            message=(
                f"Votre colis n'a pas été récupéré dans les délais au point relais {relais}. "
                "Contactez l'administrateur."
            ),
            canal='app',
        )
        messages.warning(request, "Colis signalé comme non récupéré.")
    return redirect('livraison:dashboard_gerant')


@role_required('agent_relais')
def notifications_gerant(request):
    if request.method == 'POST':
        request.user.notifications.filter(est_lue=False).update(est_lue=True)
        messages.success(request, "Toutes les notifications ont été marquées comme lues.")
        return redirect('livraison:notifications_gerant')

    return render(request, 'gerant/notifications.html', {
        **_contexte_gerant(request, 'notifications'),
        'notifications': request.user.notifications.all(),
    })


@role_required('agent_relais')
def changer_mot_de_passe_gerant(request):
    if request.method == 'POST':
        ancien = request.POST.get('ancien_mdp', '')
        nouveau = request.POST.get('nouveau_mdp', '')
        confirmation = request.POST.get('confirmation_mdp', '')

        if not request.user.check_password(ancien):
            messages.error(request, "Ancien mot de passe incorrect.")
        elif nouveau != confirmation:
            messages.error(request, "Les mots de passe ne correspondent pas.")
        elif len(nouveau) < 8:
            messages.error(request, "Le nouveau mot de passe doit contenir au moins 8 caractères.")
        elif nouveau.lower() == request.user.email.lower() or nouveau == ancien:
            messages.error(request, "Choisissez un mot de passe différent de votre email et de l'ancien.")
        else:
            request.user.set_password(nouveau)
            request.user.save()
            logout(request)
            messages.success(request, "✅ Mot de passe changé avec succès ! Reconnectez-vous avec votre nouveau mot de passe.")
            return redirect('livraison:connexion')
        return redirect('livraison:changer_mot_de_passe_gerant')

    return render(request, 'gerant/changer_mot_de_passe.html', _contexte_gerant(request, 'mot_de_passe'))


# ── PAIEMENTS ────────────────────────────────────────────────────────
# Le livreur encaisse quand c'est le client qui paie : dès qu'il a le colis en main
# (ramassage effectué), au dépôt en point relais ou après la livraison.
STATUTS_PAIEMENT_LIVREUR = ['en_cours_livraison', 'deposee_relais', 'livree']
STATUTS_PAIEMENT_DUS = ['en_attente', 'en_retard']
# Livraisons pour lesquelles le service est rendu (ou le colis attend au relais) : le paiement est dû.
STATUTS_LIVRAISON_PAIEMENT_DU = ['livree', 'deposee_relais', 'non_recupere']


def _paiements_livreur(livreur):
    return Livraison.objects.filter(
        livreur=livreur, payeur='client',
        statut__in=STATUTS_PAIEMENT_LIVREUR, statut_paiement__in=STATUTS_PAIEMENT_DUS,
    )


def _notifier_admins(titre, message):
    for admin in Utilisateur.objects.filter(role='admin'):
        Notification.objects.create(destinataire=admin, titre=titre, message=message, canal='app')


@role_required('livreur')
def confirmer_paiement_especes(request, pk):
    """Le livreur confirme avoir reçu l'argent."""
    livraison = get_object_or_404(
        _paiements_livreur(request.user.livreur_profil).select_related('client__utilisateur'),
        pk=pk, mode_paiement='especes',
    )
    if request.method == 'POST':
        livraison.enregistrer_paiement('recu_especes')
        montant = fcfa(livraison.montant)
        Notification.objects.create(
            destinataire=livraison.client.utilisateur,
            titre="Paiement reçu ✅",
            message=f"Le paiement de votre livraison #{livraison.id} a été reçu. Montant : {montant}.",
            canal='app',
        )
        _notifier_admins(
            f"Paiement reçu - Livraison #{livraison.id}",
            f"Paiement en espèces de {montant} reçu par {request.user} pour la livraison #{livraison.id}.",
        )
        messages.success(request, "✅ Paiement en espèces confirmé !")
    return redirect(_url_retour(request, 'livraison:dashboard_livreur'))


@role_required('livreur')
def confirmer_paiement_mobile(request, pk):
    """Le livreur confirme un paiement Wave / Orange Money."""
    livraison = get_object_or_404(
        _paiements_livreur(request.user.livreur_profil).select_related('client__utilisateur'),
        pk=pk, mode_paiement__in=Livraison.MODES_MOBILES,
    )
    if request.method == 'POST':
        reference = request.POST.get('reference_paiement', '').strip()[:100]
        if not reference:
            messages.error(request, "Veuillez saisir la référence de paiement.")
            return redirect(_url_retour(request, 'livraison:dashboard_livreur'))

        livraison.enregistrer_paiement('confirme_mobile', reference)
        mode = livraison.get_mode_paiement_display()
        montant = fcfa(livraison.montant)
        Notification.objects.create(
            destinataire=livraison.client.utilisateur,
            titre="Paiement confirmé ✅",
            message=f"Votre paiement {mode} de {montant} a été confirmé. Référence : {reference}.",
            canal='app',
        )
        _notifier_admins(
            f"Paiement reçu - Livraison #{livraison.id}",
            f"Paiement {mode} de {montant} confirmé par {request.user} (réf. {reference}).",
        )
        messages.success(request, f"✅ Paiement {mode} confirmé !")
    return redirect(_url_retour(request, 'livraison:dashboard_livreur'))


@role_required('agent_relais')
def confirmer_paiement_gerant(request, pk):
    """Le gérant confirme le paiement en espèces du destinataire au point relais."""
    livraison = get_object_or_404(
        _colis_du_gerant(request.user).select_related('client__utilisateur', 'point_relais'),
        pk=pk, payeur='destinataire', statut_paiement__in=STATUTS_PAIEMENT_DUS,
    )
    if request.method == 'POST':
        livraison.enregistrer_paiement('recu_especes')
        montant = fcfa(livraison.montant)
        Notification.objects.create(
            destinataire=livraison.client.utilisateur,
            titre="Paiement reçu au point relais ✅",
            message=f"Le destinataire a payé {montant} au point relais {livraison.point_relais.nom}.",
            canal='app',
        )
        _notifier_admins(
            f"Paiement reçu - Livraison #{livraison.id}",
            f"Paiement en espèces de {montant} reçu au point relais {livraison.point_relais.nom}.",
        )
        messages.success(request, "✅ Paiement confirmé ! Vous pouvez maintenant remettre le colis.")
    return redirect('livraison:dashboard_gerant')


@role_required('admin')
def marquer_paiement_en_retard(request, pk):
    livraison = get_object_or_404(
        Livraison.objects.select_related('client__utilisateur'),
        pk=pk, statut_paiement='en_attente', statut__in=STATUTS_LIVRAISON_PAIEMENT_DU,
    )
    if request.method == 'POST':
        livraison.statut_paiement = 'en_retard'
        livraison.save(update_fields=['statut_paiement'])
        Notification.objects.create(
            destinataire=livraison.client.utilisateur,
            titre=f"❌ Paiement en retard — livraison #{livraison.id}",
            message=(
                f"Le paiement de {fcfa(livraison.montant)} pour votre livraison #{livraison.id} "
                "n'a pas encore été reçu. Merci de régulariser rapidement."
            ),
            canal='app',
        )
        messages.warning(request, f"Paiement de la livraison #{livraison.id} marqué en retard.")
    return redirect('livraison:dashboard_admin')


# ── GESTION DES COMPTES (LIVREURS / CLIENTS) ─────────────────────────
@role_required('admin')
def detail_utilisateur(request, pk):
    utilisateur = get_object_or_404(Utilisateur, pk=pk)
    if utilisateur.role == 'admin':
        messages.error(request, "Impossible de consulter un compte administrateur.")
        return redirect('livraison:dashboard_admin')

    return render(request, 'admin/detail_utilisateur.html', {
        'utilisateur_cible': utilisateur,
    })


@role_required('admin')
def activer_compte(request, user_id):
    utilisateur = get_object_or_404(Utilisateur, pk=user_id)

    if request.method == 'POST':
        if utilisateur.role == 'admin':
            messages.error(request, "Action non autorisée sur un compte administrateur.")
        else:
            utilisateur.est_actif = True
            utilisateur.save()
            messages.success(request, f"Le compte de {utilisateur} a été activé.")

    return redirect(_url_retour(request, 'livraison:dashboard_admin'))


@role_required('admin')
def desactiver_compte(request, user_id):
    utilisateur = get_object_or_404(Utilisateur, pk=user_id)

    if request.method == 'POST':
        if utilisateur.role == 'admin':
            messages.error(request, "Action non autorisée sur un compte administrateur.")
        else:
            utilisateur.est_actif = False
            utilisateur.save()
            messages.success(request, f"Le compte de {utilisateur} a été désactivé.")

    return redirect(_url_retour(request, 'livraison:dashboard_admin'))


@role_required('admin')
def supprimer_compte(request, user_id):
    utilisateur = get_object_or_404(Utilisateur, pk=user_id)

    if request.method == 'POST':
        if utilisateur.role == 'admin':
            messages.error(request, "Action non autorisée sur un compte administrateur.")
            return redirect(_url_retour(request, 'livraison:dashboard_admin'))
        nom = str(utilisateur)
        utilisateur.delete()
        messages.success(request, f"Le compte de {nom} a été supprimé.")
        return redirect(_url_retour(request, 'livraison:dashboard_admin'))

    return redirect(_url_retour(request, 'livraison:dashboard_admin'))


# ── MESSAGERIE ───────────────────────────────────────────────────────
@login_required
def envoyer_message(request, pk):
    livraison = get_object_or_404(Livraison, pk=pk)
    if not _peut_voir_livraison(request.user, livraison):
        messages.error(request, "Accès non autorisé.")
        return _redirection_selon_role(request.user)

    if request.method == 'POST':
        contenu = request.POST.get('contenu', '').strip()
        if contenu:
            if request.user.role == 'livreur':
                destinataire = livraison.client.utilisateur
            else:
                destinataire = livraison.livreur.utilisateur if livraison.livreur else None

            if destinataire:
                Message.objects.create(
                    livraison=livraison,
                    expediteur=request.user,
                    destinataire=destinataire,
                    contenu=contenu,
                )
            else:
                messages.warning(request, "Aucun livreur n'est encore assigné à cette livraison.")

    return redirect('livraison:detail_livraison', pk=pk)


# ── SUIVI EN TEMPS RÉEL DES LIVREURS ─────────────────────────────────
DELAI_POSITION_PERIMEE = timedelta(minutes=2)


def api_role_required(*roles):
    """Équivalent JSON de role_required : 401 si non connecté, 403 si mauvais rôle."""
    def decorateur(vue):
        @wraps(vue)
        def enveloppe(request, *args, **kwargs):
            if not request.user.is_authenticated:
                return JsonResponse({'erreur': "Authentification requise."}, status=401)
            if request.user.role not in roles:
                return JsonResponse({'erreur': "Accès non autorisé."}, status=403)
            return vue(request, *args, **kwargs)
        return enveloppe
    return decorateur


def _libelle_vehicule(livreur):
    vehicule = getattr(livreur, 'vehicule', None)
    if vehicule:
        return str(vehicule)
    if livreur.possede_vehicule_perso:
        libelle = livreur.get_type_vehicule_perso_display() or "Véhicule personnel"
        if livreur.immatriculation_perso:
            libelle += f" — {livreur.immatriculation_perso}"
        return libelle
    return "—"


def _position_json(position):
    if position is None:
        return None
    date_locale = timezone.localtime(position.date_heure)
    return {
        'latitude': position.latitude,
        'longitude': position.longitude,
        'precision': position.precision,
        'vitesse': position.vitesse,
        'date_heure': date_locale.isoformat(),
        'heure': date_locale.strftime('%H:%M:%S'),
        # Calculé côté serveur pour ne pas dépendre de l'horloge du navigateur
        'age_secondes': int((timezone.now() - position.date_heure).total_seconds()),
    }


def _float_ou_none(valeur):
    if valeur in (None, ''):
        return None
    try:
        return float(valeur)
    except (TypeError, ValueError):
        return None


@api_role_required('livreur')
def api_enregistrer_position(request):
    if request.method != 'POST':
        return JsonResponse({'erreur': "Méthode non autorisée."}, status=405)

    if request.content_type == 'application/json':
        try:
            donnees = json.loads(request.body or b'{}')
        except ValueError:
            return JsonResponse({'erreur': "JSON invalide."}, status=400)
        if not isinstance(donnees, dict):
            return JsonResponse({'erreur': "JSON invalide."}, status=400)
    else:
        donnees = request.POST

    latitude = _float_ou_none(donnees.get('latitude'))
    longitude = _float_ou_none(donnees.get('longitude'))
    if latitude is None or longitude is None or not (-90 <= latitude <= 90) or not (-180 <= longitude <= 180):
        return JsonResponse({'erreur': "Coordonnées invalides."}, status=400)

    livreur = request.user.livreur_profil
    livraison = livreur.livraisons.filter(statut__in=STATUTS_MISSION_EN_COURS).first()
    if livraison is None:
        return JsonResponse({'erreur': "Aucune livraison en cours."}, status=400)

    position = PositionLivreur.objects.create(
        livreur=livreur,
        livraison=livraison,
        latitude=latitude,
        longitude=longitude,
        precision=_float_ou_none(donnees.get('precision')),
        vitesse=_float_ou_none(donnees.get('vitesse')),
    )
    return JsonResponse({
        'ok': True,
        'livraison_id': livraison.id,
        'statut': livraison.statut,
        'position': _position_json(position),
    }, status=201)


@api_role_required('client', 'admin')
def api_position_livraison(request, pk):
    livraison = Livraison.objects.select_related('client').filter(pk=pk).first()
    if livraison is None:
        return JsonResponse({'erreur': "Livraison introuvable."}, status=404)
    if request.user.role == 'client' and livraison.client.utilisateur_id != request.user.id:
        return JsonResponse({'erreur': "Ce colis ne vous appartient pas."}, status=403)

    return JsonResponse({
        'livraison_id': livraison.id,
        'statut': livraison.statut,
        'statut_libelle': livraison.get_statut_display(),
        'position': _position_json(
    livraison.livreur.positions.first() if livraison.livreur_id else livraison.positions.first()
),
    })


@api_role_required('admin')
def api_livreurs_actifs(request):
    livraisons = Livraison.objects.filter(
        statut__in=STATUTS_MISSION_EN_COURS, livreur__isnull=False
    ).select_related('livreur__utilisateur', 'livreur__vehicule').order_by('livreur_id', '-date_demande')

    resultats, livreurs_vus = [], set()
    for livraison in livraisons:
        livreur = livraison.livreur
        if livreur.pk in livreurs_vus:
            continue
        livreurs_vus.add(livreur.pk)
        position = livreur.positions.first()
        if position is None:
            continue
        resultats.append({
            'livreur_id': livreur.pk,
            'nom': str(livreur),
            'vehicule': _libelle_vehicule(livreur),
            'livraison_id': livraison.id,
            'statut_libelle': livraison.get_statut_display(),
            'position': _position_json(position),
        })
    return JsonResponse({'livreurs': resultats})


@role_required('client')
def suivre_livraison(request, pk):
    client = request.user.client_profil
    livraison = get_object_or_404(Livraison.objects.select_related('livreur__utilisateur'), pk=pk, client=client)
    return render(request, 'client/suivre_livraison.html', {
        **_contexte_client(client, 'mes_livraisons'),
        'livraison': livraison,
        'delai_perime_secondes': int(DELAI_POSITION_PERIMEE.total_seconds()),
    })


@role_required('admin')
def carte_livreurs_admin(request):
    return render(request, 'admin/carte_livreurs.html', {'section': 'carte_livreurs'})
