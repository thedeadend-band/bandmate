from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('player', '0012_add_paused_status'),
    ]

    operations = [
        migrations.AddField(
            model_name='stemseparationjob',
            name='gpu_used',
            field=models.BooleanField(default=None, null=True),
        ),
    ]
