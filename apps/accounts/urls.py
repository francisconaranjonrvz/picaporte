from django.contrib.auth.views import LoginView, LogoutView
from django.urls import path

from . import views
from .forms import LoginForm

urlpatterns = [
    path(
        "login/",
        LoginView.as_view(
            template_name="registration/login.html",
            authentication_form=LoginForm,
            redirect_authenticated_user=True,
        ),
        name="login",
    ),
    path("registro/", views.register, name="register"),
    # LogoutView solo acepta POST desde Django 5.0: en Perfil hay un formulario.
    path("logout/", LogoutView.as_view(), name="logout"),
]
