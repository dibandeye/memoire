from django.db import migrations

ANCIEN_VERS_NOUVEAU = {
    'en_mission': 'affecte',
    'maintenance': 'en_maintenance',
    'hors_service': 'en_maintenance',
}


def remapper_vers_nouveaux_etats(apps, schema_editor):
    Vehicule = apps.get_model('livraison', 'Vehicule')
    for ancien, nouveau in ANCIEN_VERS_NOUVEAU.items():
        Vehicule.objects.filter(etat=ancien).update(etat=nouveau)


def remapper_vers_anciens_etats(apps, schema_editor):
    Vehicule = apps.get_model('livraison', 'Vehicule')
    Vehicule.objects.filter(etat='affecte').update(etat='en_mission')
    Vehicule.objects.filter(etat='en_maintenance').update(etat='maintenance')


class Migration(migrations.Migration):

    dependencies = [
        ('livraison', '0002_alter_vehicule_etat'),
    ]

    operations = [
        migrations.RunPython(remapper_vers_nouveaux_etats, remapper_vers_anciens_etats),
    ]
