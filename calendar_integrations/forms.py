from django import forms
from .models import BandmateLocalCalendar


class LocalCalendarForm(forms.ModelForm):
    class Meta:
        model = BandmateLocalCalendar
        fields = ['name', 'color', 'timezone', 'write_enabled']
        labels = {'write_enabled': 'Allow calendar editors to edit events'}
        widgets = {
            'name': forms.TextInput(attrs={'class': 'form-input'}),
            'timezone': forms.TextInput(attrs={'class': 'form-input'}),
            'color': forms.TextInput(attrs={'type': 'color', 'class': 'form-input'}),
        }


class EventForm(forms.Form):
    title = forms.CharField(max_length=500)
    timezone = forms.CharField(max_length=64, initial='UTC', help_text='IANA timezone, such as America/Los_Angeles.')
    is_all_day = forms.BooleanField(required=False, label='All day')
    start = forms.CharField(required=False, label='Start time', widget=forms.TextInput(attrs={'type': 'datetime-local', 'step': '1'}))
    end = forms.CharField(required=False, label='End time', widget=forms.TextInput(attrs={'type': 'datetime-local', 'step': '1'}))
    start_date = forms.DateField(required=False, widget=forms.DateInput(attrs={'type': 'date'}))
    end_date = forms.DateField(required=False, label='End date (exclusive)', widget=forms.DateInput(attrs={'type': 'date'}), help_text='For a one-day event, choose the following day.')
    location = forms.CharField(max_length=500, required=False)
    description = forms.CharField(max_length=20000, required=False, widget=forms.Textarea(attrs={'rows': 5}))
    operation_key = forms.UUIDField(widget=forms.HiddenInput)
    version = forms.CharField(required=False, max_length=512, widget=forms.HiddenInput)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for field in self.fields.values():
            if not isinstance(field.widget, (forms.CheckboxInput, forms.HiddenInput)):
                field.widget.attrs['class'] = 'form-input'

    def clean(self):
        from datetime import datetime, time, timezone as dt_timezone
        from zoneinfo import ZoneInfo
        from .models import validate_timezone
        data = super().clean()
        zone_name = data.get('timezone', '')
        try:
            validate_timezone(zone_name)
        except forms.ValidationError as exc:
            self.add_error('timezone', exc)
            return data
        zone = ZoneInfo(zone_name)

        def aware(value):
            if value.tzinfo is not None:
                raise ValueError('Use a local date/time without an offset.')
            candidates = {value.replace(tzinfo=zone, fold=fold).astimezone(dt_timezone.utc)
                          for fold in (0, 1)
                          if value.replace(tzinfo=zone, fold=fold).astimezone(dt_timezone.utc).astimezone(zone).replace(tzinfo=None) == value}
            if len(candidates) != 1:
                raise ValueError('This local time is missing or ambiguous because of daylight saving. Choose another time, or use UTC.')
            return candidates.pop()

        try:
            if data.get('is_all_day'):
                if not data.get('start_date') or not data.get('end_date'):
                    raise ValueError('Choose a start date and an exclusive end date.')
                start = datetime.combine(data['start_date'], time.min)
                end = datetime.combine(data['end_date'], time.min)
            else:
                start = datetime.fromisoformat(data.get('start', ''))
                end = datetime.fromisoformat(data.get('end', ''))
            data['starts_at'], data['ends_at'] = aware(start), aware(end)
            if data['ends_at'] <= data['starts_at']:
                raise ValueError('The end must be after the start.')
        except ValueError as exc:
            raise forms.ValidationError(str(exc) if str(exc) else 'Enter valid start and end times.')
        return data

    def event_payload(self):
        data = self.cleaned_data
        return {**{name: data[name] for name in ['title', 'timezone', 'is_all_day', 'location', 'description']},
                'starts_at': data['starts_at'].isoformat(), 'ends_at': data['ends_at'].isoformat()}
