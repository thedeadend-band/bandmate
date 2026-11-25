import uuid
from django import forms
from player.models import Setlist
from .models import BookingResource


class ResourceForm(forms.ModelForm):
    revision = forms.IntegerField(min_value=0, widget=forms.HiddenInput)

    class Meta:
        model = BookingResource
        fields = ['name', 'kind', 'url', 'notes', 'approved_for_outreach', 'is_archived']
        widgets = {'notes': forms.Textarea(attrs={'rows': 3})}
        labels = {'approved_for_outreach': 'I reviewed this link and approve it for external outreach',
            'is_archived': 'Archive this resource'}
        help_texts = {'url': 'A link only; BandMate does not fetch or upload the resource.',
            'notes': 'Private Booking notes. These are not copied into outreach.',
            'approved_for_outreach': 'Only active approved EPK links are offered when composing a draft.'}


class GigPlanForm(forms.Form):
    revision = forms.IntegerField(min_value=0, widget=forms.HiddenInput)
    setlist = forms.ModelChoiceField(queryset=Setlist.objects.none(), required=False,
        label='Gig setlist', help_text='Link an existing setlist without changing its songs, owner or date.')
    notes = forms.CharField(required=False, max_length=5000, label='Private gig preparation notes',
        widget=forms.Textarea(attrs={'rows': 4}))

    def __init__(self, *args, group, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['setlist'].queryset = Setlist.objects.filter(group=group).order_by('name', 'pk')


class RehearsalForm(forms.Form):
    revision = forms.IntegerField(min_value=0, initial=0, widget=forms.HiddenInput)
    creation_key = forms.UUIDField(initial=uuid.uuid4, widget=forms.HiddenInput)
    date = forms.DateField(widget=forms.DateInput(attrs={'type': 'date'}),
        help_text='Appears as a day reminder in Booking Calendar milestones.')
    focus = forms.CharField(max_length=2000, label='Practice focus', widget=forms.Textarea(attrs={'rows': 3}))
    setlist = forms.ModelChoiceField(queryset=Setlist.objects.none(), required=False,
        help_text='Optional practice setlist. Changing the gig setlist does not change this selection.')

    def __init__(self, *args, group, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['setlist'].queryset = Setlist.objects.filter(group=group).order_by('name', 'pk')
