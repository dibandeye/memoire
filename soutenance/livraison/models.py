from django.db import models
from django.contrib.auth.models import AbstractBaseUser, BaseUserManager, PermissionsMixin
from datetime import timedelta

from django.utils import timezone


# ── MANAGER ────────────────────────────────────────────────────────
class UtilisateurManager(BaseUserManager):
    def create_user(self, email, password=None, **extra_fields):
        if not email:
            raise ValueError("L'email est obligatoire")
        email = self.normalize_email(email)
        user  = self.model(email=email, **extra_fields)
        user.set_password(password)
        user.save(using=self._db)
        return user

    def create_superuser(self, email, password=None, **extra_fields):
        extra_fields.setdefault('role', 'admin')
        extra_fields.setdefault('is_staff', True)
        extra_fields.setdefault('is_superuser', True)
        extra_fields.setdefault('est_actif', True)
        return self.create_user(email, password, **extra_fields)


# ── UTILISATEUR ────────────────────────────────────────────────────
class Utilisateur(AbstractBaseUser, PermissionsMixin):
    ROLES = [
        ('client',  'Client'),
        ('livreur', 'Livreur'),
        ('agent_relais', 'Gérant de point relais'),
        ('admin',   'Administrateur'),
    ]
    nom           = models.CharField(max_length=100)
    prenom        = models.CharField(max_length=100)
    email         = models.EmailField(unique=True)
    telephone     = models.CharField(max_length=20, blank=True)
    role          = models.CharField(max_length=20, choices=ROLES, default='client')
    est_actif     = models.BooleanField(default=True)
    is_staff      = models.BooleanField(default=False)
    date_creation = models.DateTimeField(default=timezone.now)

    objects = UtilisateurManager()

    USERNAME_FIELD  = 'email'
    REQUIRED_FIELDS = ['nom', 'prenom']

    class Meta:
        db_table     = 'utilisateurs'
        verbose_name = 'Utilisateur'

    def __str__(self):
        return f"{self.prenom} {self.nom}"


# ── CLIENT ─────────────────────────────────────────────────────────
class Client(models.Model):
    utilisateur = models.OneToOneField(
        Utilisateur, on_delete=models.CASCADE,
        primary_key=True, related_name='client_profil'
    )
    adresse = models.CharField(max_length=255, blank=True)
    ville   = models.CharField(max_length=100, blank=True)

    class Meta:
        db_table     = 'clients'
        verbose_name = 'Client'

    def __str__(self):
        return str(self.utilisateur)


# ── LIVREUR ────────────────────────────────────────────────────────
class Livreur(models.Model):
    TYPE_PERMIS = [
        ('moto',     'Moto'),
        ('voiture',  'Voiture'),
        ('les_deux', 'Moto et Voiture'),
    ]
    utilisateur    = models.OneToOneField(
        Utilisateur, on_delete=models.CASCADE,
        primary_key=True, related_name='livreur_profil'
    )
    num_permis     = models.CharField(max_length=50)
    type_permis    = models.CharField(max_length=20, choices=TYPE_PERMIS)
    est_disponible = models.BooleanField(default=False)
    note_moyenne   = models.FloatField(default=0.0)

    possede_vehicule_perso = models.BooleanField(default=False)
    type_vehicule_perso    = models.CharField(
        max_length=20,
        choices=[('moto', 'Moto'), ('voiture', 'Voiture')],
        blank=True, null=True,
    )
    immatriculation_perso  = models.CharField(max_length=20, blank=True, null=True)
    capacite_charge_perso  = models.FloatField(
        default=0.0, help_text="Capacité en kg du véhicule personnel"
    )

    class Meta:
        db_table     = 'livreurs'
        verbose_name = 'Livreur'

    def __str__(self):
        return str(self.utilisateur)

    def a_un_vehicule_operationnel(self):
        """Un véhicule perso déclaré ou un véhicule entreprise actuellement affecté."""
        return self.possede_vehicule_perso or hasattr(self, 'vehicule')

    def recalculer_note(self):
        from django.db.models import Avg
        moy = self.evaluations_recues.aggregate(Avg('note'))['note__avg']
        self.note_moyenne = round(moy, 2) if moy else 0.0
        self.save()


# ── ADMINISTRATEUR ─────────────────────────────────────────────────
class Administrateur(models.Model):
    utilisateur = models.OneToOneField(
        Utilisateur, on_delete=models.CASCADE,
        primary_key=True, related_name='admin_profil'
    )

    class Meta:
        db_table     = 'administrateurs'
        verbose_name = 'Administrateur'

    def __str__(self):
        return str(self.utilisateur)


# ── VEHICULE ───────────────────────────────────────────────────────
class Vehicule(models.Model):
    TYPES = [('moto', 'Moto'), ('voiture', 'Voiture')]
    ETATS = [
        ('disponible',     'Disponible - Au parking'),
        ('affecte',        'Affecté à un livreur'),
        ('en_maintenance', 'En maintenance - Au garage'),
    ]
    immatriculation = models.CharField(max_length=20, unique=True)
    type_vehicule   = models.CharField(max_length=20, choices=TYPES)
    marque          = models.CharField(max_length=100, blank=True)
    modele          = models.CharField(max_length=100, blank=True)
    couleur         = models.CharField(max_length=50, blank=True)
    photo           = models.ImageField(upload_to='vehicules/', blank=True, null=True)
    capacite_charge = models.FloatField(default=0.0)
    etat            = models.CharField(max_length=20, choices=ETATS, default='disponible')
    livreur         = models.OneToOneField(
        Livreur, on_delete=models.SET_NULL,
        null=True, blank=True, related_name='vehicule'
    )

    class Meta:
        db_table     = 'vehicules'
        verbose_name = 'Véhicule'

    def __str__(self):
        return f"{self.get_type_vehicule_display()} — {self.immatriculation}"


# ── POINT RELAIS ───────────────────────────────────────────────────
class PointRelais(models.Model):
    # Horaires fixes : Lundi-Samedi 09h-19h, Dimanche fermé
    HORAIRES      = "Lundi-Samedi 09h00-19h00"
    DELAI_GARDE   = 7  # jours pour récupérer un colis

    nom       = models.CharField(max_length=150)
    adresse   = models.CharField(max_length=255)
    ville     = models.CharField(max_length=100)
    telephone = models.CharField(max_length=20, blank=True)
    est_actif = models.BooleanField(default=True)

    class Meta:
        db_table     = 'points_relais'
        verbose_name = 'Point relais'

    def est_ouvert_aujourd_hui(self):
        # weekday() : 6 = dimanche
        return timezone.localtime().weekday() != 6

    def __str__(self):
        return f"{self.nom} — {self.ville}"


class GerantPointRelais(models.Model):
    point_relais     = models.ForeignKey(PointRelais, on_delete=models.CASCADE, related_name='gerants')
    gerant           = models.ForeignKey(Utilisateur, on_delete=models.CASCADE, related_name='points_relais_geres')
    date_affectation = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table        = 'gerants_points_relais'
        unique_together = ['point_relais', 'gerant']
        verbose_name    = 'Gérant de point relais'

    def __str__(self):
        return f"{self.gerant} — {self.point_relais}"


# ── LIVRAISON ──────────────────────────────────────────────────────
class Livraison(models.Model):
    TYPE_VEHICULE = [
        ('moto',    'Moto'),
        ('voiture', 'Voiture'),
        ('camion',  'Camion'),
    ]
    TARIFS_VEHICULE = {'moto': 2000, 'voiture': 5000, 'camion': 15000}
    MODES = [
        ('domicile',     'À domicile'),
        ('point_relais', 'Point relais'),
    ]
    STATUTS = [
        ('en_attente',         'En attente'),
        ('attribuee',          'Attribuée'),
        ('en_cours_ramassage', 'En cours de ramassage'),
        ('en_cours_livraison', 'En cours de livraison'),
        ('deposee_relais',     'Déposée au point relais'),
        ('livree',             'Livrée'),
        ('annulee',            'Annulée'),
        ('probleme',           'Problème signalé'),
        ('non_recupere',       'Non récupéré'),
    ]
    MODE_PAIEMENT = [
        ('especes',      'Espèces au livreur'),
        ('wave',         'Wave'),
        ('orange_money', 'Orange Money'),
    ]
    STATUT_PAIEMENT = [
        ('en_attente',      'En attente de paiement'),
        ('recu_especes',    'Paiement reçu en espèces'),
        ('confirme_mobile', 'Paiement confirmé Mobile Money'),
        ('en_retard',       'Paiement en retard'),
    ]
    PAYEUR = [
        ('client',       'Client (expéditeur)'),
        ('destinataire', 'Destinataire'),
    ]
    STATUTS_PAYES   = ('recu_especes', 'confirme_mobile')
    MODES_MOBILES   = ('wave', 'orange_money')

    client                 = models.ForeignKey(Client,      on_delete=models.CASCADE,  related_name='livraisons')
    livreur                = models.ForeignKey(Livreur,     on_delete=models.SET_NULL, related_name='livraisons', null=True, blank=True)
    point_relais           = models.ForeignKey(PointRelais, on_delete=models.SET_NULL, related_name='livraisons', null=True, blank=True)
    type_vehicule          = models.CharField(max_length=20,  choices=TYPE_VEHICULE, default='moto')
    type_livraison         = models.CharField(max_length=20,  choices=MODES, default='domicile')
    adresse_depart         = models.CharField(max_length=255)
    ville_depart           = models.CharField(max_length=100)
    adresse_destination    = models.CharField(max_length=255)
    ville_destination      = models.CharField(max_length=100)
    description_contenu    = models.TextField(blank=True, null=True)
    poids_estime           = models.FloatField(default=0.0)
    volume_estime          = models.FloatField(default=0.0, help_text="En m³")
    instructions_speciales = models.TextField(blank=True, null=True)
    nom_destinataire       = models.CharField(max_length=150)
    tel_destinataire       = models.CharField(max_length=20)
    statut                 = models.CharField(max_length=30, choices=STATUTS, default='en_attente')
    date_demande           = models.DateTimeField(auto_now_add=True)
    livraison_immediate    = models.BooleanField(default=True)
    date_programmee        = models.DateTimeField(null=True, blank=True)
    date_ramassage         = models.DateTimeField(null=True, blank=True)
    date_livraison         = models.DateTimeField(null=True, blank=True)
    montant                = models.FloatField(default=0.0)
    motif_annulation       = models.TextField(blank=True)

    # Suivi du stockage en point relais
    date_depot_relais        = models.DateTimeField(null=True, blank=True)
    date_limite_recuperation = models.DateTimeField(null=True, blank=True)
    rappel_j5_envoye         = models.BooleanField(default=False)
    rappel_j7_envoye         = models.BooleanField(default=False)

    # Paiement
    mode_paiement      = models.CharField(max_length=20, choices=MODE_PAIEMENT, default='especes')
    statut_paiement    = models.CharField(max_length=20, choices=STATUT_PAIEMENT, default='en_attente')
    payeur             = models.CharField(max_length=20, choices=PAYEUR, default='client')
    date_paiement      = models.DateTimeField(null=True, blank=True)
    reference_paiement = models.CharField(max_length=100, blank=True, help_text="Référence Wave ou Orange Money")

    class Meta:
        db_table  = 'livraisons'
        ordering  = ['-date_demande']
        verbose_name = 'Livraison'

    def __str__(self):
        return f"Livraison #{self.id} — {self.get_statut_display()}"

    @classmethod
    def prix_pour(cls, type_vehicule):
        return cls.TARIFS_VEHICULE.get(type_vehicule, cls.TARIFS_VEHICULE['moto'])

    @property
    def est_payee(self):
        return self.statut_paiement in self.STATUTS_PAYES

    @property
    def paiement_mobile(self):
        return self.mode_paiement in self.MODES_MOBILES

    @property
    def libelle_mode_paiement(self):
        if self.mode_paiement == 'especes' and self.payeur == 'destinataire':
            return "Espèces au gérant"
        return self.get_mode_paiement_display()

    @property
    def reference_attendue(self):
        """Référence que le client indique dans son transfert Wave / Orange Money."""
        return f"LIV-{self.id}"

    def _creer_ou_maj_facture(self):
        facture, _ = Facture.objects.get_or_create(
            livraison=self,
            defaults={
                'montant_ht':  self.montant,
                'tva':         18.0,
                'montant_ttc': round(self.montant * 1.18, 2),
            },
        )
        statut = 'payee' if self.est_payee else 'en_attente'
        if facture.statut != statut:
            facture.statut = statut
            facture.save(update_fields=['statut'])
        return facture

    def enregistrer_paiement(self, statut_paiement, reference=''):
        """Marque la livraison comme payée et génère (ou solde) sa facture."""
        self.statut_paiement = statut_paiement
        self.date_paiement = timezone.now()
        if reference:
            self.reference_paiement = reference
        self.save(update_fields=['statut_paiement', 'date_paiement', 'reference_paiement'])
        self._creer_ou_maj_facture()

    def calculer_montant(self):
        self.montant = self.prix_pour(self.type_vehicule)
        self.save()

    def maj_statut(self, nouveau_statut):
        self.statut = nouveau_statut
        if nouveau_statut == 'en_cours_ramassage':
            self.date_ramassage = timezone.now()
        elif nouveau_statut == 'deposee_relais':
            self.date_depot_relais = timezone.now()
            self.date_limite_recuperation = self.date_depot_relais + timedelta(days=PointRelais.DELAI_GARDE)
        elif nouveau_statut == 'livree':
            self.date_livraison = timezone.now()
        self.save()
        if nouveau_statut == 'livree':
            self._creer_ou_maj_facture()
        if nouveau_statut == 'deposee_relais' and self.point_relais:
            date_limite = timezone.localtime(self.date_limite_recuperation).strftime('%d/%m/%Y')
            Notification.objects.create(
                destinataire = self.client.utilisateur,
                titre        = "Colis disponible au point relais",
                message      = (
                    f"Votre colis #{self.id} est disponible au {self.point_relais.nom}, "
                    f"{self.point_relais.adresse}. Horaires : {PointRelais.HORAIRES}. "
                    f"Vous avez {PointRelais.DELAI_GARDE} jours pour le récupérer (avant le {date_limite})."
                ),
                canal        = 'app'
            )
            return
        if nouveau_statut == 'livree' and self.date_depot_relais and self.point_relais:
            Notification.objects.create(
                destinataire = self.client.utilisateur,
                titre        = f"Colis #{self.id} récupéré ✅",
                message      = f"Le destinataire a récupéré votre colis au point relais {self.point_relais.nom}.",
                canal        = 'app'
            )
            return
        Notification.objects.create(
            destinataire = self.client.utilisateur,
            titre        = f"Livraison #{self.id}",
            message      = f"Statut mis à jour : {self.get_statut_display()}",
            canal        = 'app'
        )


# ── EXCLUSION LIVREUR / LIVRAISON ─────────────────────────────────────
class LivraisonExclusion(models.Model):
    """Empêche qu'un livreur ayant annulé une livraison pour un motif qui lui
    est propre (ex : problème de position) ne se la voie réattribuer."""
    livraison = models.ForeignKey(
        Livraison, on_delete=models.CASCADE, related_name='exclusions'
    )
    livreur   = models.ForeignKey(
        Livreur, on_delete=models.CASCADE, related_name='livraisons_exclues'
    )
    motif     = models.CharField(max_length=50)
    date      = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table        = 'livraison_exclusions'
        unique_together = ['livraison', 'livreur']
        verbose_name    = 'Exclusion livreur/livraison'

    def __str__(self):
        return f"{self.livreur} exclu de la livraison #{self.livraison_id}"


# ── POSITION LIVREUR (suivi en temps réel) ─────────────────────────
class PositionLivreur(models.Model):
    livreur = models.ForeignKey('Livreur', on_delete=models.CASCADE, related_name='positions')
    livraison = models.ForeignKey('Livraison', null=True, blank=True, on_delete=models.SET_NULL, related_name='positions')
    latitude = models.FloatField()
    longitude = models.FloatField()
    precision = models.FloatField(null=True, blank=True)
    vitesse = models.FloatField(null=True, blank=True)
    date_heure = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-date_heure']

    def __str__(self):
        return f"{self.livreur} ({self.latitude}, {self.longitude}) — {self.date_heure:%d/%m/%Y %H:%M:%S}"


# ── MESSAGE ────────────────────────────────────────────────────────
class Message(models.Model):
    livraison    = models.ForeignKey(Livraison,   on_delete=models.CASCADE, related_name='messages')
    expediteur   = models.ForeignKey(Utilisateur, on_delete=models.CASCADE, related_name='messages_envoyes')
    destinataire = models.ForeignKey(Utilisateur, on_delete=models.CASCADE, related_name='messages_recus')
    contenu      = models.TextField()
    date_envoi   = models.DateTimeField(auto_now_add=True)
    est_lu       = models.BooleanField(default=False)

    class Meta:
        db_table  = 'messages'
        ordering  = ['date_envoi']
        verbose_name = 'Message'

    def __str__(self):
        return f"Message #{self.id}"


# ── EVALUATION ─────────────────────────────────────────────────────
class Evaluation(models.Model):
    livraison   = models.OneToOneField(Livraison, on_delete=models.CASCADE, related_name='evaluation')
    client      = models.ForeignKey(Client,  on_delete=models.CASCADE, related_name='evaluations_donnees')
    livreur     = models.ForeignKey(Livreur, on_delete=models.CASCADE, related_name='evaluations_recues')
    note        = models.IntegerField(choices=[(i, i) for i in range(1, 6)])
    commentaire = models.TextField(blank=True)
    date        = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table     = 'evaluations'
        verbose_name = 'Évaluation'

    def save(self, *args, **kwargs):
        super().save(*args, **kwargs)
        self.livreur.recalculer_note()

    def __str__(self):
        return f"Note {self.note}/5 — Livraison #{self.livraison.id}"


# ── FACTURE ────────────────────────────────────────────────────────
class Facture(models.Model):
    STATUTS = [
        ('en_attente', 'En attente'),
        ('payee',      'Payée'),
        ('annulee',    'Annulée'),
    ]
    livraison     = models.OneToOneField(Livraison, on_delete=models.CASCADE, related_name='facture')
    numero        = models.CharField(max_length=50, unique=True, blank=True)
    montant_ht    = models.FloatField()
    tva           = models.FloatField(default=18.0)
    montant_ttc   = models.FloatField()
    date_emission = models.DateTimeField(auto_now_add=True)
    statut        = models.CharField(max_length=20, choices=STATUTS, default='en_attente')

    class Meta:
        db_table     = 'factures'
        verbose_name = 'Facture'

    def save(self, *args, **kwargs):
        if not self.numero:
            annee = timezone.now().year
            self.numero = f"FAC-{annee}-{str(self.livraison_id).zfill(5)}"
        super().save(*args, **kwargs)

    def __str__(self):
        return self.numero


# ── NOTIFICATION ───────────────────────────────────────────────────
class Notification(models.Model):
    CANAUX = [
        ('app',   'Application'),
        ('email', 'Email'),
        ('sms',   'SMS'),
    ]
    destinataire = models.ForeignKey(Utilisateur, on_delete=models.CASCADE, related_name='notifications')
    titre        = models.CharField(max_length=150)
    message      = models.TextField()
    canal        = models.CharField(max_length=10, choices=CANAUX, default='app')
    est_lue      = models.BooleanField(default=False)
    date_envoi   = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table     = 'notifications'
        ordering     = ['-date_envoi']
        verbose_name = 'Notification'

    def __str__(self):
        return f"{self.titre} → {self.destinataire}"