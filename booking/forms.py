from django import forms
from django.conf import settings
from .models import BookingSettings


class BookingProfileForm(forms.ModelForm):
    class Meta:
        model = BookingSettings
        fields = ['search_center', 'radius_miles', 'genres', 'search_latitude', 'search_longitude', 'excluded_cities', 'target_years']
        labels = {'search_center': 'Search location', 'radius_miles': 'Radius (straight-line miles)'}
        widgets = {
            'search_center': forms.TextInput(attrs={'class': 'form-input'}),
            'genres': forms.TextInput(attrs={'class': 'form-input'}),
            'radius_miles': forms.NumberInput(attrs={'class': 'form-input'}),
        }
        help_texts = {'search_latitude': 'Optional manually verified center coordinates; no geocoder runs automatically.',
            'excluded_cities': 'Comma-separated city names. These are flagged in ranking and can be filtered out.',
            'target_years': 'Comma-separated years, such as 2026,2027. Historical editions remain separate.'}

    def clean(self):
        cleaned = super().clean()
        if (cleaned.get('search_latitude') is None) != (cleaned.get('search_longitude') is None):
            raise forms.ValidationError('Provide both center coordinates, or neither.')
        years = cleaned.get('target_years', '').strip()
        if years:
            parts = [part.strip() for part in years.split(',')]
            if len(parts) > 10 or any(not part.isdigit() or not 1900 <= int(part) <= 2200 for part in parts):
                raise forms.ValidationError('Enter up to ten valid target years separated by commas.')
            cleaned['target_years'] = ','.join(str(year) for year in sorted({int(part) for part in parts}))
        else:
            cleaned['target_years'] = self.instance.target_years
        return cleaned


class OpenAIKeyForm(forms.Form):
    api_key = forms.CharField(max_length=512, strip=True, widget=forms.PasswordInput(
        render_value=False, attrs={'class': 'form-input', 'autocomplete': 'new-password'}),
        help_text='Stored encrypted for this band. It will not be displayed again.')
    project_label = forms.CharField(max_length=120, required=False,
        widget=forms.TextInput(attrs={'class': 'form-input'}), help_text='A label for your records; this does not verify OpenAI project ownership.')
    project_id = forms.CharField(max_length=120, required=False, widget=forms.TextInput(attrs={'class': 'form-input'}),
        help_text='Optional project identifier for your records. The API key determines access.')

    def clean_api_key(self):
        value = self.cleaned_data['api_key']
        if not value.startswith('sk-') or len(value) < 20 or any(char.isspace() for char in value):
            raise forms.ValidationError('Enter a valid OpenAI API key.')
        return value


class AIPolicyForm(forms.ModelForm):
    ai_model = forms.ChoiceField(required=False, label='Allowed model')

    class Meta:
        model = BookingSettings
        fields = ['ai_enabled', 'ai_model', 'monthly_budget_usd', 'daily_request_limit', 'max_output_tokens']
        labels = {'monthly_budget_usd': 'Monthly estimated cost limit (USD)', 'daily_request_limit': 'Daily request limit (UTC)'}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['ai_model'].choices = [('', 'Choose a model')] + [(m, m) for m in settings.BOOKING_OPENAI_MODELS]
        for field in self.fields.values():
            field.widget.attrs['class'] = 'form-input'

    def clean(self):
        cleaned = super().clean()
        if cleaned.get('ai_enabled') and (not cleaned.get('ai_model') or not cleaned.get('monthly_budget_usd') or not cleaned.get('daily_request_limit')):
            raise forms.ValidationError('Choose an allowed model and positive cost and request limits before enabling AI.')
        return cleaned
