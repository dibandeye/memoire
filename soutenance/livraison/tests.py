import json
from datetime import timedelta

from django.test import TestCase
from django.urls import reverse

from .models import Client, Livraison, Livreur, PositionLivreur, Utilisateur


class SuiviTempsReelTests(TestCase):
    def setUp(self):
        self.admin = Utilisateur.objects.create_superuser('admin@test.sn', 'mdp', nom='Admin', prenom='A')
        u_client = Utilisateur.objects.create_user('client@test.sn', 'mdp', nom='Diop', prenom='Awa', role='client')
        u_autre = Utilisateur.objects.create_user('autre@test.sn', 'mdp', nom='Fall', prenom='Bob', role='client')
        u_livreur = Utilisateur.objects.create_user('livreur@test.sn', 'mdp', nom='Ndiaye', prenom='Moussa', role='livreur')
        self.client_profil = Client.objects.create(utilisateur=u_client)
        Client.objects.create(utilisateur=u_autre)
        self.livreur = Livreur.objects.create(
            utilisateur=u_livreur, num_permis='P1', type_permis='moto',
            possede_vehicule_perso=True, type_vehicule_perso='moto', immatriculation_perso='DK-1234-A',
        )
        self.livraison = Livraison.objects.create(
            client=self.client_profil, livreur=self.livreur, statut='en_cours_livraison',
            adresse_depart='Rue 1', ville_depart='Dakar', adresse_destination='Rue 2',
            ville_destination='Pikine', nom_destinataire='X', tel_destinataire='77',
        )

    def _poster(self, **donnees):
        return self.client.post(reverse('livraison:api_enregistrer_position'),
                                json.dumps(donnees), content_type='application/json')

    # ── POST /api/position/ ──────────────────────────────────────
    def test_livreur_enregistre_position(self):
        self.client.login(email='livreur@test.sn', password='mdp')
        r = self._poster(latitude=14.7, longitude=-17.45, precision=8, vitesse=3.2)
        self.assertEqual(r.status_code, 201)
        p = PositionLivreur.objects.get()
        self.assertEqual((p.livraison, p.latitude, p.vitesse), (self.livraison, 14.7, 3.2))

    def test_position_sans_livraison_en_cours_400(self):
        self.livraison.statut = 'livree'
        self.livraison.save()
        self.client.login(email='livreur@test.sn', password='mdp')
        self.assertEqual(self._poster(latitude=14.7, longitude=-17.45).status_code, 400)
        self.assertFalse(PositionLivreur.objects.exists())

    def test_position_coordonnees_invalides_400(self):
        self.client.login(email='livreur@test.sn', password='mdp')
        self.assertEqual(self._poster(latitude='abc', longitude=-17.45).status_code, 400)
        self.assertEqual(self._poster(latitude=95, longitude=-17.45).status_code, 400)

    def test_position_refusee_aux_autres_roles(self):
        self.assertEqual(self._poster(latitude=1, longitude=1).status_code, 401)
        self.client.login(email='client@test.sn', password='mdp')
        self.assertEqual(self._poster(latitude=1, longitude=1).status_code, 403)

    def test_position_exige_csrf(self):
        from django.test import Client as ClientHttp
        http = ClientHttp(enforce_csrf_checks=True)
        http.login(email='livreur@test.sn', password='mdp')
        r = http.post(reverse('livraison:api_enregistrer_position'),
                      json.dumps({'latitude': 1, 'longitude': 1}), content_type='application/json')
        self.assertEqual(r.status_code, 403)

    # ── GET /api/livraison/<id>/position/ ────────────────────────
    def test_client_proprietaire_voit_derniere_position(self):
        PositionLivreur.objects.create(livreur=self.livreur, livraison=self.livraison, latitude=1, longitude=1)
        PositionLivreur.objects.create(livreur=self.livreur, livraison=self.livraison, latitude=2, longitude=2)
        self.client.login(email='client@test.sn', password='mdp')
        d = self.client.get(reverse('livraison:api_position_livraison', args=[self.livraison.id])).json()
        self.assertEqual(d['statut'], 'en_cours_livraison')
        self.assertEqual(d['position']['latitude'], 2)
        self.assertIn('heure', d['position'])

    def test_position_perimee_signalee_par_age(self):
        p = PositionLivreur.objects.create(livreur=self.livreur, livraison=self.livraison, latitude=1, longitude=1)
        PositionLivreur.objects.filter(pk=p.pk).update(date_heure=p.date_heure - timedelta(minutes=3))
        self.client.login(email='client@test.sn', password='mdp')
        d = self.client.get(reverse('livraison:api_position_livraison', args=[self.livraison.id])).json()
        self.assertGreater(d['position']['age_secondes'], 120)

    def test_autre_client_403_admin_200(self):
        url = reverse('livraison:api_position_livraison', args=[self.livraison.id])
        self.client.login(email='autre@test.sn', password='mdp')
        self.assertEqual(self.client.get(url).status_code, 403)
        self.client.login(email='admin@test.sn', password='mdp')
        r = self.client.get(url)
        self.assertEqual(r.status_code, 200)
        self.assertIsNone(r.json()['position'])

    # ── GET /api/livreurs-actifs/ ────────────────────────────────
    def test_livreurs_actifs_admin_uniquement(self):
        PositionLivreur.objects.create(livreur=self.livreur, livraison=self.livraison, latitude=1, longitude=1)
        url = reverse('livraison:api_livreurs_actifs')
        self.client.login(email='client@test.sn', password='mdp')
        self.assertEqual(self.client.get(url).status_code, 403)
        self.client.login(email='admin@test.sn', password='mdp')
        livreurs = self.client.get(url).json()['livreurs']
        self.assertEqual(len(livreurs), 1)
        self.assertEqual(livreurs[0]['nom'], 'Moussa Ndiaye')
        self.assertEqual(livreurs[0]['vehicule'], 'Moto — DK-1234-A')
        self.assertEqual(livreurs[0]['livraison_id'], self.livraison.id)

    def test_livreur_sans_livraison_en_cours_absent(self):
        PositionLivreur.objects.create(livreur=self.livreur, latitude=1, longitude=1)
        self.livraison.statut = 'livree'
        self.livraison.save()
        self.client.login(email='admin@test.sn', password='mdp')
        self.assertEqual(self.client.get(reverse('livraison:api_livreurs_actifs')).json()['livreurs'], [])

    # ── Pages ────────────────────────────────────────────────────
    def test_pages_rendues(self):
        self.client.login(email='livreur@test.sn', password='mdp')
        self.assertContains(self.client.get(reverse('livraison:dashboard_livreur')), 'Partager ma position')

        self.client.login(email='client@test.sn', password='mdp')
        self.assertContains(self.client.get(reverse('livraison:suivre_livraison', args=[self.livraison.id])), 'leaflet@1.9.4')
        self.assertContains(self.client.get(reverse('livraison:mes_livraisons')),
                            reverse('livraison:suivre_livraison', args=[self.livraison.id]))

        self.client.login(email='autre@test.sn', password='mdp')
        self.assertEqual(self.client.get(reverse('livraison:suivre_livraison', args=[self.livraison.id])).status_code, 404)

        self.client.login(email='admin@test.sn', password='mdp')
        r = self.client.get(reverse('livraison:carte_livreurs_admin'))
        self.assertContains(r, 'carteLivreurs')
        self.assertContains(r, 'Carte des livreurs')

    def test_trajet_sur_detail_admin(self):
        for i in range(3):
            PositionLivreur.objects.create(livreur=self.livreur, livraison=self.livraison, latitude=14 + i, longitude=-17)
        self.client.login(email='admin@test.sn', password='mdp')
        r = self.client.get(reverse('livraison:detail_livraison', args=[self.livraison.id]))
        self.assertEqual(r.context['trajet'], [[14, -17], [15, -17], [16, -17]])
        self.assertContains(r, 'carteTrajet')
