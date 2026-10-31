from django.db import migrations, models

import api.fields


class Migration(migrations.Migration):
    """Coffre des clés d'API des fournisseurs, par utilisateur (chiffré au repos).

    Aucune donnée à reprendre : les clés n'étaient jamais stockées côté serveur
    (elles vivaient dans le localStorage du navigateur, que le frontend efface
    désormais sans l'envoyer — chacun ressaisit sa clé une fois).
    """

    dependencies = [
        ('api', '0004_mediaobject_storage'),
    ]

    operations = [
        migrations.CreateModel(
            name='ProviderCredential',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False,
                                           verbose_name='ID')),
                ('user_email', models.EmailField(db_index=True, max_length=255)),
                ('provider', models.CharField(max_length=40)),
                ('field', models.CharField(max_length=40)),
                ('value', api.fields.EncryptedTextField()),
                ('updated_at', models.DateTimeField(auto_now=True)),
            ],
            options={
                'db_table': 'provider_credentials',
                'constraints': [models.UniqueConstraint(
                    fields=('user_email', 'provider', 'field'),
                    name='uniq_credential_per_user_provider_field')],
            },
        ),
    ]
