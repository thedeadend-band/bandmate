from django.conf import settings
from django.db import models


class Setlist(models.Model):
    name = models.CharField(max_length=255)
    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='setlists',
    )
    date = models.DateField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-updated_at']

    def __str__(self):
        return self.name


class SetlistEntry(models.Model):
    setlist = models.ForeignKey(
        Setlist,
        on_delete=models.CASCADE,
        related_name='entries',
    )
    song_name = models.CharField(max_length=500, blank=True, default='')
    position = models.PositiveIntegerField()
    is_break = models.BooleanField(default=False)

    class Meta:
        ordering = ['position']
        unique_together = [('setlist', 'position')]

    def __str__(self):
        if self.is_break:
            return f'{self.setlist.name} #{self.position}: (Break)'
        return f'{self.setlist.name} #{self.position}: {self.song_name}'


class SiteSettings(models.Model):
    google_calendar_url = models.URLField(max_length=1000, blank=True, default='')

    class Meta:
        verbose_name_plural = 'Site settings'

    def __str__(self):
        return 'Site Settings'

    @classmethod
    def load(cls):
        obj, _ = cls.objects.get_or_create(pk=1)
        return obj
