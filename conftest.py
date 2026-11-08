import pytest


@pytest.fixture(autouse=True)
def plain_static_storage(settings):
    """Production uses WhiteNoise's manifest storage, which needs
    `collectstatic` to have run. Tests render admin pages without that
    step, so they use Django's plain static storage instead."""
    settings.STORAGES = {
        **settings.STORAGES,
        "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"},
    }
