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
    reply_to = forms.IntegerField(required=False, widget=forms.HiddenInput)


class ReportForm(StyledFormMixin, forms.ModelForm):
    class Meta:
        model = Report
        fields = ("severity", "reason")
        widgets = {"reason": forms.Textarea(attrs={"rows": 5, "placeholder": "Describe what happened."})}


class SettingsForm(forms.ModelForm):
    class Meta:
        model = User
        fields = ["display_name", "mockup_fun_name", "anonymous_intro", "photo"]
        labels = {
            "mockup_fun_name": "Mockup fun name",
            "anonymous_intro": "Anonymous intro",
        }
        help_texts = {
            "mockup_fun_name": "Optional. Shown before identity reveal. Disrespectful names can be reported.",
            "anonymous_intro": "Optional. Maximum 60 words. Shown only to active anonymous connections before reveal.",
        }
        widgets = {
            "anonymous_intro": forms.Textarea(attrs={"rows": 4, "placeholder": "A tiny anonymous intro, if you want one."}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["photo"].widget.attrs.update({
            "accept": "image/*",
        })

    def clean_mockup_fun_name(self):
        return self.cleaned_data.get("mockup_fun_name", "").strip()

    def clean_anonymous_intro(self):
        value = self.cleaned_data.get("anonymous_intro", "").strip()
        if len(value.split()) > 60:
            raise forms.ValidationError("Keep your anonymous intro to 60 words or fewer.")
        return value
