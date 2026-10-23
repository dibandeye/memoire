from django.db import migrations, models


# Correspondance ancien type de service -> nouveau type de véhicule.
SERVICE_VERS_VEHICULE = {'colis': 'moto', 'marchandise': 'voiture', 'demenagement': 'camion'}


def migrer_donnees(apps, schema_editor):
    Livraison = apps.get_model('livraison', 'Livraison')
    for livraison in Livraison.objects.all():
        livraison.type_vehicule = SERVICE_VERS_VEHICULE.get(livraison.type_service, 'moto')
        livraison.date_programmee = livraison.date_souhaitee
        livraison.livraison_immediate = livraison.date_souhaitee is None
        livraison.save(update_fields=['type_vehicule', 'date_programmee', 'livraison_immediate'])


def restaurer_donnees(apps, schema_editor):
    Livraison = apps.get_model('livraison', 'Livraison')
    vehicule_vers_service = {v: k for k, v in SERVICE_VERS_VEHICULE.items()}
    for livraison in Livraison.objects.all():
        livraison.type_service = vehicule_vers_service.get(livraison.type_vehicule, 'colis')
        livraison.date_souhaitee = None if livraison.livraison_immediate else livraison.date_programmee
        livraison.save(update_fields=['type_service', 'date_souhaitee'])


class Migration(migrations.Migration):

    dependencies = [
        ('livraison', '0008_livraisonexclusion'),
    ]

    operations = [
        migrations.AddField(
            model_name='livraison',
            name='type_vehicule',
            field=models.CharField(choices=[('moto', 'Moto'), ('voiture', 'Voiture'), ('camion', 'Camion')], default='moto', max_length=20),
        ),
        migrations.AddField(
            model_name='livraison',
            name='livraison_immediate',
            field=models.BooleanField(default=True),
        ),
        migrations.AddField(
            model_name='livraison',
            name='date_programmee',
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AlterField(
            model_name='livraison',
            name='type_service',
            field=models.CharField(blank=True, default='colis', max_length=20),
        ),
        migrations.RunPython(migrer_donnees, restaurer_donnees),
        migrations.RemoveField(model_name='livraison', name='type_service'),
        migrations.RemoveField(model_name='livraison', name='date_souhaitee'),
        migrations.RemoveField(model_name='livraison', name='poids_kg'),
        migrations.AlterField(
            model_name='livraison',
            name='description_contenu',
            field=models.TextField(blank=True, null=True),
        ),
        migrations.AlterField(
            model_name='livraison',
            name='instructions_speciales',
            field=models.TextField(blank=True, null=True),
        ),
    ]
