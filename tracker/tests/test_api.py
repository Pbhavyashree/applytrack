from django.core.files.uploadedfile import SimpleUploadedFile
from rest_framework.test import APIClient

from tracker.models import Application, Stage

from .conftest import DAY


def test_anonymous_is_rejected(db):
    assert APIClient().get("/api/applications/").status_code in (401, 403)


def test_create_via_api(api, user):
    res = api.post("/api/applications/", {"company_name": "Kiezbank", "role": "Backend Engineer",
                                          "stage": "applied", "source": "referral"}, format="json")
    assert res.status_code == 201, res.json()
    body = res.json()
    assert body["company"] == "Kiezbank" and body["stage"] == "applied" and body["applied_on"]
    assert set(body["allowed_next_stages"]) >= {"screening", "rejected"}


def test_create_cannot_start_at_offer(api):
    res = api.post("/api/applications/", {"company_name": "X", "role": "Y", "stage": "offer"}, format="json")
    assert res.status_code == 400


def test_update_cannot_change_stage(api, make_app):
    app = make_app()
    res = api.patch(f"/api/applications/{app.pk}/", {"stage": "offer", "notes": "hi"}, format="json")
    assert res.status_code == 200
    app.refresh_from_db()
    assert app.stage == Stage.APPLIED and app.notes == "hi"


def test_move_action(api, make_app):
    app = make_app()
    res = api.post(f"/api/applications/{app.pk}/move/", {"stage": "screening", "note": "Recruiter call"}, format="json")
    assert res.status_code == 200
    assert res.json()["history"][-1]["note"] == "Recruiter call"


def test_invalid_move_is_409(api, make_app):
    res = api.post(f"/api/applications/{make_app().pk}/move/", {"stage": "accepted"}, format="json")
    assert res.status_code == 409


def test_users_cannot_see_each_others_data(api, make_app, other_user):
    theirs = make_app(owner=other_user, company="Secret Co")
    assert api.get(f"/api/applications/{theirs.pk}/").status_code == 404
    assert api.post(f"/api/applications/{theirs.pk}/move/", {"stage": "rejected"}, format="json").status_code == 404
    assert api.get("/api/applications/").json()["count"] == 0
    assert api.get("/api/companies/").json()["count"] == 0
    assert api.get("/api/stats/").json()["applied"] == 0


def test_interview_on_someone_elses_application_is_refused(api, make_app, other_user):
    theirs = make_app(owner=other_user)
    res = api.post("/api/interviews/", {"application": theirs.pk, "kind": "technical",
                                        "scheduled_at": "2026-11-10T10:00:00Z"}, format="json")
    assert res.status_code == 400


def test_filters(api, make_app):
    make_app(role="Backend Engineer", company="Kiezbank")
    make_app(role="Data Engineer", company="Brightgrid", stage=Stage.SAVED)
    assert api.get("/api/applications/?stage=saved").json()["count"] == 1
    assert api.get("/api/applications/?q=kiez").json()["count"] == 1
    assert api.get("/api/applications/?q=engineer").json()["count"] == 2


def test_follow_ups_endpoint(api, make_app):
    make_app(applied_on=DAY)
    assert len(api.get("/api/applications/follow-ups/").json()) == 1


def test_stats_endpoint_validates_since(api):
    assert api.get("/api/stats/?since=last-week").status_code == 400
    assert api.get("/api/stats/?since=2026-10-01").status_code == 200


def test_import_endpoint(api, user):
    upload = SimpleUploadedFile("t.csv", b"Company,Role,Status\nKiezbank,Dev,Applied\n", content_type="text/csv")
    res = api.post("/api/applications/import/", {"file": upload}, format="multipart")
    assert res.json()["created"] == 1
    assert Application.objects.filter(owner=user).count() == 1


def test_duplicate_company_name_is_rejected(api, make_app):
    make_app(company="Kiezbank")
    assert api.post("/api/companies/", {"name": "KIEZBANK"}, format="json").status_code == 400


def test_company_with_applications_cannot_be_deleted(api, make_app):
    app = make_app()
    assert api.delete(f"/api/companies/{app.company_id}/").status_code == 409
