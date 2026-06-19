from django import forms
from django.conf import settings
from django.contrib.auth.forms import AuthenticationForm, UserCreationForm

from .models import Report, User


class StyledFormMixin:
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for field in self.fields.values():
            field.widget.attrs.setdefault("class", "field-input")


class SignupForm(StyledFormMixin, UserCreationForm):
    class Meta:
        model = User
        fields = ("display_name", "email", "gender")

    def clean_email(self):
        email = self.cleaned_data["email"].lower()
        domain = email.rsplit("@", 1)[-1]
        if domain not in settings.GIM_ALLOWED_EMAIL_DOMAINS:
            raise forms.ValidationError("Use your GIM institutional email address.")
        return email



class OTPForm(StyledFormMixin, forms.Form):
    code = forms.CharField(min_length=6, max_length=6, label="Six-digit code")


class LoginForm(StyledFormMixin, AuthenticationForm):
    username = forms.EmailField(label="GIM email")


class MessageForm(StyledFormMixin, forms.Form):
    body = forms.CharField(
        max_length=2000,
        label="",
        widget=forms.Textarea(attrs={"rows": 2, "placeholder": "Write a message..."}),
    )


class ReportForm(StyledFormMixin, forms.ModelForm):
    class Meta:
        model = Report
        fields = ("severity", "reason")
        widgets = {"reason": forms.Textarea(attrs={"rows": 5, "placeholder": "Describe what happened."})}


class SettingsForm(forms.ModelForm):
    class Meta:
        model = User
        # Ensure 'photo' is included in this list!
        fields = ['display_name', 'photo']
