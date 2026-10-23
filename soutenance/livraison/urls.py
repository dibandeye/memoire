from django.urls import path

from . import views

app_name = 'livraison'

urlpatterns = [
    path('', views.connexion, name='accueil'),
    path('connexion/', views.connexion, name='connexion'),
    path('inscription/', views.inscription, name='inscription'),
    path('deconnexion/', views.deconnexion, name='deconnexion'),

    # Espace client
    path('client/', views.dashboard_client, name='dashboard_client'),
    path('client/mes-livraisons/', views.mes_livraisons, name='mes_livraisons'),
    path('client/livraisons/nouvelle/', views.choisir_vehicule, name='choisir_vehicule'),
    path('client/livraisons/nouvelle/<str:type_vehicule>/', views.creer_livraison, name='creer_livraison'),
    path('client/livraison/<int:pk>/modifier/', views.modifier_livraison, name='modifier_livraison'),
    path('client/notifications/', views.notifications_client, name='notifications_client'),
    path('client/profil/', views.profil_client, name='profil_client'),

    # Livraisons (communes à tous les rôles autorisés)
    path('livraisons/<int:pk>/', views.detail_livraison, name='detail_livraison'),
    path('livraisons/<int:pk>/annuler/', views.annuler_livraison, name='annuler_livraison'),
    path('livraisons/<int:pk>/evaluer/', views.evaluer_livreur, name='evaluer_livreur'),
    path('livraisons/<int:pk>/statut/', views.maj_statut_livraison, name='maj_statut_livraison'),
    path('livraisons/<int:pk>/message/', views.envoyer_message, name='envoyer_message'),
    path('livraisons/<int:pk>/attribuer/', views.attribuer_livreur, name='attribuer_livreur'),

    # Espace livreur
    path('livreur/', views.dashboard_livreur, name='dashboard_livreur'),
    path('livreur/livraison/<int:pk>/annuler/', views.annuler_livraison_livreur, name='annuler_livraison_livreur'),
    path('livreur/vehicule/', views.vehicule_livreur, name='vehicule_livreur'),
    path('livreur/livraison/<int:pk>/paiement-especes/', views.confirmer_paiement_especes, name='confirmer_paiement_especes'),
    path('livreur/livraison/<int:pk>/paiement-mobile/', views.confirmer_paiement_mobile, name='confirmer_paiement_mobile'),
    path('livreur/notifications/', views.notifications_livreur, name='notifications_livreur'),
    path('livreur/notifications/marquer-lues/', views.marquer_notifications_lues, name='marquer_notifications_lues'),
    path('livreur/profil/', views.profil_livreur, name='profil_livreur'),

    # Espace gérant de point relais
    path('gerant/', views.dashboard_gerant, name='dashboard_gerant'),
    path('gerant/colis/<int:pk>/recupere/', views.marquer_colis_recupere, name='marquer_colis_recupere'),
    path('gerant/colis/<int:pk>/non-recupere/', views.signaler_colis_non_recupere, name='signaler_colis_non_recupere'),
    path('gerant/colis/<int:pk>/paiement/', views.confirmer_paiement_gerant, name='confirmer_paiement_gerant'),
    path('gerant/notifications/', views.notifications_gerant, name='notifications_gerant'),
    path('gerant/changer-mot-de-passe/', views.changer_mot_de_passe_gerant, name='changer_mot_de_passe_gerant'),

    # ── Espace administrateur ────────────────────────────────────────
    path('admin-panel/', views.dashboard_admin, name='dashboard_admin'),

    # Livraisons
    path('admin-panel/livraisons/', views.liste_livraisons_admin, name='liste_livraisons_admin'),
    path('admin-panel/livraisons/<int:pk>/annuler/', views.annuler_livraison_admin, name='annuler_livraison_admin'),
    path('admin-panel/livraisons/<int:pk>/paiement-en-retard/', views.marquer_paiement_en_retard, name='marquer_paiement_en_retard'),

    # Livreurs
    path('admin-panel/livreurs/', views.liste_livreurs_admin, name='liste_livreurs_admin'),

    # Véhicules
    path('admin-panel/vehicules/', views.liste_vehicules_admin, name='liste_vehicules_admin'),
    path('admin-panel/vehicules/recherche/', views.recherche_vehicules, name='recherche_vehicules'),
    path('admin-panel/vehicules/ajouter/', views.ajouter_vehicule, name='ajouter_vehicule'),
    path('admin-panel/vehicules/<int:pk>/', views.detail_vehicule, name='detail_vehicule'),
    path('admin-panel/vehicules/<int:pk>/modifier/', views.modifier_vehicule, name='modifier_vehicule'),
    path('admin-panel/vehicules/<int:pk>/supprimer/', views.supprimer_vehicule, name='supprimer_vehicule'),
    path('admin-panel/vehicules/affecter/', views.affecter_vehicule, name='affecter_vehicule'),
    path('admin-panel/vehicules/liberer/<int:vehicule_id>/', views.liberer_vehicule, name='liberer_vehicule'),
    path('admin-panel/vehicules/<int:vehicule_id>/maintenance/', views.mettre_en_maintenance, name='mettre_en_maintenance'),
    path('admin-panel/vehicules/<int:vehicule_id>/marquer-disponible/', views.marquer_disponible, name='marquer_disponible'),

    # Clients
    path('admin-panel/clients/', views.liste_clients_admin, name='liste_clients_admin'),

    # Points relais
    path('admin-panel/points-relais/', views.liste_points_relais, name='liste_points_relais'),
    path('admin-panel/points-relais/ajouter/', views.ajouter_point_relais, name='ajouter_point_relais'),
    path('admin-panel/points-relais/<int:pk>/', views.detail_point_relais, name='detail_point_relais'),
    path('admin-panel/points-relais/<int:pk>/modifier/', views.modifier_point_relais, name='modifier_point_relais'),
    path('admin-panel/points-relais/<int:pk>/activer/', views.activer_point_relais, name='activer_point_relais'),
    path('admin-panel/points-relais/<int:pk>/desactiver/', views.desactiver_point_relais, name='desactiver_point_relais'),
    path('admin-panel/points-relais/<int:pk>/supprimer/', views.supprimer_point_relais, name='supprimer_point_relais'),
    path('admin-panel/points-relais/<int:pk>/affecter-gerant/', views.affecter_gerant, name='affecter_gerant'),
    path('admin-panel/points-relais/<int:pk>/retirer-gerant/<int:gerant_id>/', views.retirer_gerant, name='retirer_gerant'),
    path('admin-panel/points-relais/<int:pk>/colis/<int:livraison_id>/recupere/', views.marquer_colis_recupere_admin, name='marquer_colis_recupere_admin'),

    # Gérants de points relais
    path('admin-panel/gerants/', views.liste_gerants, name='liste_gerants'),
    path('admin-panel/gerants/ajouter/', views.ajouter_gerant, name='ajouter_gerant'),
    path('admin-panel/gerants/<int:pk>/', views.detail_gerant, name='detail_gerant'),
    path('admin-panel/gerants/<int:pk>/modifier/', views.modifier_gerant, name='modifier_gerant'),
    path('admin-panel/gerants/<int:pk>/activer-desactiver/', views.activer_desactiver_gerant, name='activer_desactiver_gerant'),
    path('admin-panel/gerants/<int:pk>/supprimer/', views.supprimer_gerant, name='supprimer_gerant'),
    path('admin-panel/gerants/<int:pk>/affecter/', views.affecter_gerant_point_relais, name='affecter_gerant_point_relais'),
    path('admin-panel/gerants/<int:pk>/retirer/<int:relais_id>/', views.retirer_gerant_point_relais, name='retirer_gerant_point_relais'),

    # Paramètres
    path('admin-panel/parametres/', views.parametres_admin, name='parametres_admin'),

    # Comptes (livreurs et clients) — voir / valider / activer / désactiver / supprimer
    path('admin-panel/utilisateurs/<int:pk>/', views.detail_utilisateur, name='detail_utilisateur'),
    path('admin-panel/valider/<int:user_id>/', views.valider_livreur, name='valider_livreur'),
    path('admin-panel/desactiver/<int:user_id>/', views.desactiver_compte, name='desactiver_compte'),
    path('admin-panel/activer/<int:user_id>/', views.activer_compte, name='activer_compte'),
    path('admin-panel/supprimer/<int:user_id>/', views.supprimer_compte, name='supprimer_compte'),
]
