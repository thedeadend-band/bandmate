import re
from django import forms
from django.core.validators import validate_email
from .models import BookingContact, BookingEmailTemplate, BookingDraft

PLACEHOLDERS = {'band_name', 'venue_name', 'event_name', 'organizer_name', 'prior_contact_context',
    'year', 'epk_url', 'available_dates', 'contact_name'}
PATTERN = re.compile(r'{{\s*([a-z_]+)\s*}}')


def validate_template(text):
    names = set(PATTERN.findall(text))
    if names - PLACEHOLDERS or '{{' in PATTERN.sub('', text) or '}}' in PATTERN.sub('', text):
        raise forms.ValidationError('Use only the documented placeholders; template code is not supported.')
    return text


class TemplateForm(forms.Form):
    name = forms.CharField(max_length=200)
    category = forms.ChoiceField(choices=BookingEmailTemplate._meta.get_field('category').choices)
    subject = forms.CharField(max_length=300, validators=[validate_template])
    body = forms.CharField(max_length=12000, widget=forms.Textarea, validators=[validate_template])
    revision = forms.IntegerField(required=False, widget=forms.HiddenInput)

    def clean_subject(self):
        value = self.cleaned_data['subject']
        if '\n' in value or '\r' in value:
            raise forms.ValidationError('Use a single-line subject.')
        return value


class ComposeForm(forms.Form):
    contact = forms.ModelChoiceField(queryset=BookingContact.objects.none())
    template = forms.ModelChoiceField(queryset=BookingEmailTemplate.objects.none(), required=False)
    epk_url = forms.URLField(required=False, max_length=1000)
    available_dates = forms.CharField(required=False, max_length=1000)

    def __init__(self, *args, group, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['contact'].queryset = BookingContact.objects.filter(group=group, is_archived=False).exclude(email='')
        self.fields['template'].queryset = BookingEmailTemplate.objects.filter(group=group, is_archived=False)


def addresses(value):
    items = [v.strip().lower() for v in value.split(',') if v.strip()]
    if len(items) > 4 or len(items) != len(set(items)):
        raise forms.ValidationError('Use at most four distinct addresses, separated by commas.')
    for address in items:
        validate_email(address)
        if '\n' in address or '\r' in address:
            raise forms.ValidationError('Invalid address.')
    return items


class DraftForm(forms.ModelForm):
    cc = forms.CharField(required=False, max_length=1000)
    bcc = forms.CharField(required=False, max_length=1000)
    revision = forms.IntegerField(widget=forms.HiddenInput)

    class Meta:
        model = BookingDraft
        fields = ['to', 'cc', 'bcc', 'subject', 'body']
        widgets = {'body': forms.Textarea(attrs={'rows': 14})}

    def clean_cc(self):
        return addresses(self.cleaned_data['cc'])

    def clean_bcc(self):
        return addresses(self.cleaned_data['bcc'])

    def clean_subject(self):
        value = self.cleaned_data['subject']
        if '\r' in value or '\n' in value:
            raise forms.ValidationError('Use a single-line subject.')
        return value

    def clean(self):
        data = super().clean()
        recipients = [data.get('to', '').lower(), *data.get('cc', []), *data.get('bcc', [])]
        if len(recipients) != len(set(recipients)):
            raise forms.ValidationError('Do not repeat recipients between To, Cc and Bcc.')
        return data
