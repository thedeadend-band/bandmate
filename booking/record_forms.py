from django import forms
from django.contrib.auth.models import User
from django.core.exceptions import ValidationError
from .models import (BookingOrganization, BookingVenue, BookingContact, BookingEventSeries,
                     BookingOpportunity, BookingFollowUp, BookingSuppression)
from .services.records import canonical_url, normalized


class RecordForm(forms.ModelForm):
    revision = forms.IntegerField(required=False, widget=forms.HiddenInput)

    def __init__(self, *args, group, **kwargs):
        super().__init__(*args, **kwargs)
        self.instance.group = group
        for name, field in self.fields.items():
            if isinstance(field, forms.ModelChoiceField):
                field.queryset = field.queryset.filter(group_memberships__group=group, is_active=True).distinct() if field.queryset.model == User else field.queryset.filter(group=group, is_archived=False)
            if isinstance(field, forms.DateField):
                field.widget = forms.DateInput(attrs={'type': 'date'}, format='%Y-%m-%d')
            if isinstance(field.widget, forms.Textarea):
                field.widget.attrs['rows'] = 4
            field.widget.attrs['class'] = 'form-input'
        self.initial['revision'] = self.instance.revision

    def clean_website(self):
        value = self.cleaned_data.get('website', '')
        return canonical_url(value) if value else ''


COMMON = ['name', 'city', 'website', 'tags', 'notes']


class OrganizationForm(RecordForm):
    class Meta:
        model = BookingOrganization
        fields = COMMON + ['category']


class VenueForm(RecordForm):
    class Meta:
        model = BookingVenue
        fields = COMMON + ['organization', 'address', 'latitude', 'longitude']


class ContactForm(RecordForm):
    class Meta:
        model = BookingContact
        fields = COMMON + ['organization', 'venue', 'email', 'role', 'phone']


class SeriesForm(RecordForm):
    class Meta:
        model = BookingEventSeries
        fields = COMMON + ['organization', 'venue', 'season']


class OpportunityForm(RecordForm):
    class Meta:
        model = BookingOpportunity
        fields = COMMON + ['organization', 'venue', 'contact', 'series', 'event_year', 'category', 'stage',
            'priority', 'event_date', 'application_open_date', 'verified_deadline', 'deadline_source',
            'deadline_excerpt', 'predicted_window_start', 'predicted_window_end', 'prediction_reason', 'assigned_to']
        labels = {'deadline_source': 'Application dates source', 'deadline_excerpt': 'Application dates excerpt'}
        help_texts = {'verified_deadline': 'Use only a published deadline for this event cycle. Previous-year dates belong in a predicted window.',
            'application_open_date': 'Use only a published opening date, with source evidence below.',
            'deadline_excerpt': 'Quote the source text identifying the opening date or deadline and relevant application cycle.',
            'predicted_window_start': 'A forecast only; never shown as an official deadline.'}


FORMS = {'organizations': OrganizationForm, 'venues': VenueForm, 'contacts': ContactForm,
         'series': SeriesForm, 'opportunities': OpportunityForm}


class FollowUpForm(forms.ModelForm):
    class Meta:
        model = BookingFollowUp
        fields = ['due_date', 'note']
        widgets = {'due_date': forms.DateInput(attrs={'type': 'date', 'class': 'form-input'}),
                   'note': forms.TextInput(attrs={'class': 'form-input'})}


class SuppressionForm(forms.ModelForm):
    def validate_unique(self):
        # The service intentionally reactivates an existing normalized rule.
        pass
    class Meta:
        model = BookingSuppression
        fields = ['kind', 'value', 'reason', 'do_not_contact']
        help_texts = {'value': 'Email, domain, exact URL, record UUID, or name|city.',
                     'do_not_contact': 'Keeps outreach blocked even if this blacklist entry is restored.'}

    def clean(self):
        values = super().clean()
        kind, value = values.get('kind'), values.get('value', '').strip()
        if kind == 'url':
            value = canonical_url(value)
        elif kind == 'email':
            forms.EmailField().clean(value)
            value = value.lower()
        elif kind == 'domain':
            import re
            if not re.fullmatch(r'[A-Za-z0-9.-]+\.[A-Za-z]{2,63}', value):
                raise ValidationError('Enter a domain without a URL path or email address.')
            value = value.lower().strip('.')
        elif kind == 'identity':
            import uuid
            try:
                value = str(uuid.UUID(value))
            except ValueError:
                raise ValidationError('Enter a valid record UUID.') from None
        else:
            if '|' not in value:
                raise ValidationError('Use name|city for a name match.')
            value = '|'.join(normalized(part) for part in value.split('|', 1))
        values['value'] = value
        return values
