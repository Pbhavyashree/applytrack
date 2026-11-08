from .models import UserSettings


def theme(request):
    """Expose the signed-in user's theme to every template, so the page is
    rendered in the right colours from the first paint (no flash)."""
    return {"theme": UserSettings.theme_for(request.user)}
