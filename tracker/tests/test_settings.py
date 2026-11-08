from django.test import Client

from tracker.models import UserSettings


def signed_in(user):
    c = Client()
    c.force_login(user)
    return c


def test_default_theme_follows_the_device(user):
    html = signed_in(user).get("/").content.decode()
    assert "data-theme" not in html.split(">", 2)[1]


def test_saving_dark_theme_applies_to_every_page(user):
    c = signed_in(user)
    res = c.post("/settings/", {"theme": "dark"})
    assert res.status_code == 302
    assert UserSettings.objects.get(user=user).theme == "dark"
    for url in ["/", "/import/", "/settings/"]:
        assert '<html lang="en" data-theme="dark">' in c.get(url).content.decode()


def test_light_theme_overrides_a_dark_device(user):
    c = signed_in(user)
    c.post("/settings/", {"theme": "light"})
    assert 'data-theme="light"' in c.get("/").content.decode()


def test_invalid_theme_is_rejected(user):
    c = signed_in(user)
    c.post("/settings/", {"theme": "neon"})
    assert not UserSettings.objects.filter(user=user, theme="neon").exists()


def test_theme_is_per_user(user, other_user):
    signed_in(user).post("/settings/", {"theme": "dark"})
    assert "data-theme" not in signed_in(other_user).get("/").content.decode().split(">", 2)[1]


def test_settings_requires_login(db):
    assert Client().get("/settings/").status_code == 302


def test_login_page_uses_device_theme(db):
    assert "data-theme" not in Client().get("/login/").content.decode().split(">", 2)[1]
