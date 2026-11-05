from django import forms

from .models import Application, Interview, Stage


def job_url_field():
    return forms.URLField(required=False, max_length=500, label="Job link", assume_scheme="https")


class ApplicationForm(forms.ModelForm):
    job_url = job_url_field()
    company_name = forms.CharField(max_length=120, label="Company")
    stage = forms.ChoiceField(
        choices=[(Stage.SAVED, "Saved — not applied yet"), (Stage.APPLIED, "Applied")],
        initial=Stage.APPLIED,
        label="Status",
    )

    class Meta:
        model = Application
        fields = ["role", "job_url", "location", "source", "applied_on", "notes"]
        widgets = {
            "applied_on": forms.DateInput(attrs={"type": "date"}),
            "notes": forms.Textarea(attrs={"rows": 3}),
        }
        labels = {"job_url": "Job link", "applied_on": "Applied on (leave blank for today)"}

    field_order = ["company_name", "role", "job_url", "location", "source", "stage", "applied_on", "notes"]


class EditApplicationForm(forms.ModelForm):
    job_url = job_url_field()

    class Meta:
        model = Application
        fields = ["role", "job_url", "location", "source", "next_follow_up", "notes"]
        widgets = {
            "next_follow_up": forms.DateInput(attrs={"type": "date"}),
            "notes": forms.Textarea(attrs={"rows": 4}),
        }
        labels = {"job_url": "Job link", "next_follow_up": "Follow up on"}


class MoveForm(forms.Form):
    stage = forms.ChoiceField(choices=Stage.choices)
    note = forms.CharField(max_length=300, required=False)


class InterviewForm(forms.ModelForm):
    class Meta:
        model = Interview
        fields = ["kind", "scheduled_at", "with_whom", "notes"]
        widgets = {
            "scheduled_at": forms.DateTimeInput(attrs={"type": "datetime-local"}, format="%Y-%m-%dT%H:%M"),
            "notes": forms.Textarea(attrs={"rows": 2}),
        }
        labels = {"with_whom": "With"}


class ImportForm(forms.Form):
    file = forms.FileField(label="CSV file")

    def clean_file(self):
        f = self.cleaned_data["file"]
        if f.size > 2 * 1024 * 1024:
            raise forms.ValidationError("Keep the file under 2 MB.")
        try:
            return f.read().decode("utf-8-sig")
        except UnicodeDecodeError as exc:
            raise forms.ValidationError("Save the spreadsheet as CSV (UTF-8).") from exc
