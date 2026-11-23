import re
from datetime import date, datetime, time, timedelta, timezone
from django import forms
from .services.records import canonical_url


class GmailImportForm(forms.Form):
    start_date = forms.DateField(initial=date(2024, 1, 1), widget=forms.DateInput(attrs={'type': 'date'}))
    end_date = forms.DateField(widget=forms.DateInput(attrs={'type': 'date'}))
    keywords = forms.CharField(max_length=300, initial='booking gig festival concert entertainment')
    max_messages = forms.IntegerField(min_value=1, max_value=100, initial=50)
    retention_days = forms.IntegerField(min_value=1, max_value=365, initial=90)

    def clean(self):
        values = super().clean()
        if values.get('start_date') and values.get('end_date') and values['end_date'] < values['start_date']:
            raise forms.ValidationError('The end date must follow the start date.')
        keywords = values.get('keywords', '').split()
        if not keywords or len(keywords) > 20 or any(not re.fullmatch(r'[\w@.-]{1,50}', word, flags=re.ASCII) for word in keywords):
            raise forms.ValidationError('Use up to 20 simple keywords separated by spaces. Gmail operators are not accepted.')
        return values

    def scope(self):
        values = self.cleaned_data
        start = int(datetime.combine(values['start_date'], time.min, timezone.utc).timestamp())
        end = int(datetime.combine(values['end_date'] + timedelta(days=1), time.min, timezone.utc).timestamp())
        query = 'after:' + str(start - 1) + ' before:' + str(end) + ' {' + ' '.join(values['keywords'].split()) + '}'
        return {'start_date': values['start_date'].isoformat(), 'end_date': values['end_date'].isoformat(),
                'query': query, 'keywords': values['keywords'], 'max_messages': values['max_messages'],
                'retention_days': values['retention_days'], 'ai_processing': False}


class DiscoveryForm(forms.Form):
    url = forms.URLField(max_length=1000, label='Public organizer, venue or event URL')

    def clean_url(self):
        url = canonical_url(self.cleaned_data['url'])
        if not url.startswith('https://'):
            raise forms.ValidationError('Use a public HTTPS URL.')
        return url
