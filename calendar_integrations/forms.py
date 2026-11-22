from django import forms
from .models import BandmateLocalCalendar


class LocalCalendarForm(forms.ModelForm):
    class Meta:
        model = BandmateLocalCalendar
        fields = ['name', 'color', 'timezone', 'write_enabled']
        labels = {'write_enabled': 'Allow calendar editors to edit events when event editing is available'}
        widgets = {
            'name': forms.TextInput(attrs={'class': 'form-input'}),
            'timezone': forms.TextInput(attrs={'class': 'form-input'}),
            'color': forms.TextInput(attrs={'type': 'color', 'class': 'form-input'}),
        }
