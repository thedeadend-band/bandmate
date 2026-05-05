from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('player', '0011_rename_tempo_key_label'),
    ]

    operations = [
        migrations.AlterField(
            model_name='stemseparationjob',
            name='status',
            field=models.CharField(
                choices=[
                    ('queued', 'Queued'),
                    ('processing', 'Processing'),
                    ('paused', 'Paused'),
                    ('done', 'Done'),
                    ('failed', 'Failed'),
                ],
                default='queued',
                max_length=20,
            ),
        ),
    ]
