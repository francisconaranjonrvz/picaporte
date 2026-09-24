"""Alta de cuentas (registro abierto, con tope `MAX_USERS`)."""

from django.conf import settings
from django.contrib import messages
from django.contrib.auth import get_user_model, login
from django.contrib.auth.decorators import login_not_required
from django.shortcuts import redirect, render

from .forms import RegisterForm


def registration_open() -> bool:
    return get_user_model().objects.count() < settings.MAX_USERS


@login_not_required
def register(request):
    if request.user.is_authenticated:
        return redirect("perfil")
    if not registration_open():
        return render(request, "registration/register.html", {"closed": True}, status=403)
    form = RegisterForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        user = form.save()
        login(request, user, backend="django.contrib.auth.backends.ModelBackend")
        messages.success(
            request,
            "Cuenta creada. Sube tu CV y guarda el perfil: buscaremos empresas para ti.",
        )
        return redirect("perfil")
    return render(request, "registration/register.html", {"form": form})
