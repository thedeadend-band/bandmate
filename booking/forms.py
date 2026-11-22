from django import forms
from .models import BookingSettings


class BookingProfileForm(forms.ModelForm):
    class Meta:
        model = BookingSettings
        fields = ['search_center', 'radius_miles', 'genres']
        labels = {'search_center': 'Search location', 'radius_miles': 'Radius (straight-line miles)'}
        widgets = {
            'search_center': forms.TextInput(attrs={'class': 'form-input'}),
            'genres': forms.TextInput(attrs={'class': 'form-input'}),
            'radius_miles': forms.NumberInput(attrs={'class': 'form-input'}),
        }
