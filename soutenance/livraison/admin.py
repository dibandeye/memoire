from django.contrib import admin

# Register your models here.
from django.contrib import admin
from django.contrib.auth.admin import UserAdmin
from .models import (
    Utilisateur, Client, Livreur, Administrateur,
    Vehicule, Livraison, Message, Evaluation,
    Facture, Notification, PointRelais, GerantPointRelais
)

@admin.register(Utilisateur)
class UtilisateurAdmin(UserAdmin):
    list_display    = ['email', 'nom', 'prenom', 'role', 'est_actif']
    list_filter     = ['role', 'est_actif']
    search_fields   = ['email', 'nom', 'prenom']
    ordering        = ['email']
    fieldsets = (
        (None,           {'fields': ('email', 'password')}),
        ('Informations', {'fields': ('nom', 'prenom', 'telephone', 'role', 'est_actif')}),
        ('Permissions',  {'fields': ('is_staff', 'is_superuser')}),
    )
    add_fieldsets = (
        (None, {'fields': ('email', 'password1', 'password2', 'nom', 'prenom', 'role')}),
    )

@admin.register(Livraison)
class LivraisonAdmin(admin.ModelAdmin):
    list_display    = ['id', 'client', 'livreur', 'type_vehicule', 'statut', 'montant', 'date_demande']
    list_filter     = ['statut', 'type_vehicule', 'type_livraison']
    search_fields   = ['client__utilisateur__nom', 'livreur__utilisateur__nom']
    readonly_fields = ['date_demande', 'date_ramassage', 'date_livraison']

@admin.register(Vehicule)
class VehiculeAdmin(admin.ModelAdmin):
    list_display = ['immatriculation', 'type_vehicule', 'marque', 'etat', 'livreur']
    list_filter  = ['type_vehicule', 'etat']

@admin.register(Livreur)
class LivreurAdmin(admin.ModelAdmin):
    list_display = ['utilisateur', 'type_permis', 'est_disponible', 'note_moyenne']
    list_filter  = ['est_disponible', 'type_permis']

@admin.register(Client)
class ClientAdmin(admin.ModelAdmin):
    list_display = ['utilisateur', 'adresse', 'ville']

@admin.register(Administrateur)
class AdministrateurAdmin(admin.ModelAdmin):
    list_display = ['utilisateur']

@admin.register(PointRelais)
class PointRelaisAdmin(admin.ModelAdmin):
    list_display = ['nom', 'ville', 'telephone', 'est_actif']
    list_filter  = ['est_actif']

@admin.register(GerantPointRelais)
class GerantPointRelaisAdmin(admin.ModelAdmin):
    list_display = ['gerant', 'point_relais', 'date_affectation']

@admin.register(Message)
class MessageAdmin(admin.ModelAdmin):
    list_display = ['livraison', 'expediteur', 'destinataire', 'date_envoi', 'est_lu']
    list_filter  = ['est_lu']

@admin.register(Evaluation)
class EvaluationAdmin(admin.ModelAdmin):
    list_display = ['livraison', 'client', 'livreur', 'note', 'date']
    list_filter  = ['note']

@admin.register(Facture)
class FactureAdmin(admin.ModelAdmin):
    list_display = ['numero', 'livraison', 'montant_ht', 'montant_ttc', 'statut', 'date_emission']
    list_filter  = ['statut']

@admin.register(Notification)
class NotificationAdmin(admin.ModelAdmin):
    list_display = ['titre', 'destinataire', 'canal', 'est_lue', 'date_envoi']
    list_filter  = ['canal', 'est_lue']