import datetime

import pytest
from django.utils import timezone

from tracker import services
from tracker.models import Application, Company, Source, Stage, StageChange

from .conftest import DAY


def test_create_applied_sets_dates_and_history(make_app):
    app = make_app(applied_on=DAY)
    assert app.stage == Stage.APPLIED
    assert app.applied_on == DAY
    assert app.next_follow_up == DAY + datetime.timedelta(days=7)
    assert list(app.changes.values_list("from_stage", "to_stage")) == [("", "saved"), ("saved", "applied")]


def test_create_saved_ignores_applied_date(make_app):
    app = make_app(stage=Stage.SAVED, applied_on=DAY)
    assert app.applied_on is None and app.next_follow_up is None


def test_create_rejects_late_starting_stage(make_app):
    with pytest.raises(services.TransitionError):
        make_app(stage=Stage.OFFER)


def test_company_is_reused_case_insensitively(make_app, user):
    make_app(company="Kiezbank")
    make_app(company="kiezbank ", role="Platform Engineer")
    assert Company.objects.filter(owner=user).count() == 1


def test_company_names_are_per_user(make_app, other_user):
    make_app(company="Kiezbank")
    make_app(company="Kiezbank", owner=other_user)
    assert Company.objects.count() == 2


@pytest.mark.parametrize("path", [
    [Stage.SCREENING, Stage.INTERVIEW, Stage.OFFER, Stage.ACCEPTED],
    [Stage.INTERVIEW, Stage.REJECTED],
    [Stage.GHOSTED, Stage.SCREENING],
    [Stage.WITHDRAWN],
])
def test_valid_paths(make_app, path):
    app = make_app()
    for stage in path:
        app = services.move_to_stage(app, stage)
    assert app.stage == path[-1]
    assert app.changes.count() == 2 + len(path)


@pytest.mark.parametrize("path", [
    [Stage.OFFER],                       # skipping straight to offer
    [Stage.REJECTED, Stage.INTERVIEW],   # reopening a closed application
    [Stage.SCREENING, Stage.APPLIED],    # going backwards
    [Stage.SCREENING, Stage.SCREENING],  # no-op move
])
def test_invalid_paths_raise_and_record_nothing(make_app, path):
    app = make_app()
    with pytest.raises(services.TransitionError):
        for stage in path:
            app = services.move_to_stage(app, stage)
    app.refresh_from_db()
    assert app.changes.count() == 2 + len(path) - 1


def test_reply_clears_follow_up(make_app):
    app = services.move_to_stage(make_app(), Stage.REJECTED)
    assert app.next_follow_up is None


def test_follow_ups_due_only_active_and_own(make_app, user, other_user):
    due = make_app(applied_on=DAY)
    make_app(role="Later", applied_on=DAY + datetime.timedelta(days=30))
    services.move_to_stage(make_app(role="Closed", applied_on=DAY), Stage.WITHDRAWN)
    make_app(owner=other_user, applied_on=DAY)
    assert list(services.follow_ups_due(user, today=DAY + datetime.timedelta(days=8))) == [due]


def test_mark_ghosted_moves_only_stale_applied(make_app):
    stale, fresh = make_app(role="Stale"), make_app(role="Fresh")
    StageChange.objects.filter(application=stale).update(changed_at=timezone.now() - datetime.timedelta(days=40))
    moved = services.mark_ghosted(days=30)
    assert [a.pk for a in moved] == [stale.pk]
    fresh.refresh_from_db()
    assert fresh.stage == Stage.APPLIED


def test_stats_count_history_not_just_current_stage(make_app, user):
    a = make_app(source=Source.LINKEDIN)
    for s in (Stage.SCREENING, Stage.INTERVIEW, Stage.REJECTED):
        a = services.move_to_stage(a, s)
    make_app(role="Silent", source=Source.LINKEDIN)
    make_app(role="Ref", source=Source.REFERRAL)
    stats = services.pipeline_stats(user)
    assert (stats.applied, stats.responded, stats.interviewing, stats.offers) == (3, 1, 1, 0)
    assert stats.response_rate == round(1 / 3, 3)
    linkedin = next(s for s in stats.by_source if s.source == Source.LINKEDIN)
    assert (linkedin.applied, linkedin.responded, linkedin.response_rate) == (2, 1, 0.5)


def test_median_days_to_response(make_app, user):
    today = timezone.localdate()
    for days in (4, 10, 30):
        app = make_app(role=f"R{days}", applied_on=today - datetime.timedelta(days=days))
        services.move_to_stage(app, Stage.REJECTED)
    assert services.pipeline_stats(user).median_days_to_response == 10


def test_stats_since_filters_by_applied_date(make_app, user):
    make_app(role="Old", applied_on=DAY - datetime.timedelta(days=60))
    make_app(role="New", applied_on=DAY)
    assert services.pipeline_stats(user, since=DAY).applied == 1


def test_stats_with_no_data(user):
    stats = services.pipeline_stats(user)
    assert stats.applied == 0 and stats.response_rate is None and stats.median_days_to_response is None


# --- CSV import -----------------------------------------------------------

CSV = """Company,Position,Status,Date applied,Source,Link
Kiezbank,Backend Engineer,Rejected,14.10.2026,LinkedIn,https://example.com/1
Brightgrid,Platform Engineer,Applied,2026-10-20,StepStone,
Taktwerk,Integration Developer,,,,
Hafen Systems,Data Engineer,On hold,2026-10-21,,
"""


def test_import_maps_columns_and_values(user):
    result = services.import_csv(user, CSV)
    assert (result.created, result.skipped) == (3, 0)
    assert result.errors == ["Row 5: unknown status 'On hold'"]
    kiez = Application.objects.get(company__name="Kiezbank")
    assert (kiez.stage, kiez.applied_on, kiez.source) == (Stage.REJECTED, datetime.date(2026, 10, 14), Source.LINKEDIN)
    assert Application.objects.get(company__name="Brightgrid").source == Source.JOB_BOARD
    assert Application.objects.get(company__name="Taktwerk").stage == Stage.SAVED


def test_import_is_idempotent(user):
    services.import_csv(user, CSV)
    again = services.import_csv(user, CSV)
    assert (again.created, again.skipped) == (0, 3)
    assert Application.objects.count() == 3


def test_imported_later_stages_are_flagged_and_excluded_from_timing(user):
    services.import_csv(user, CSV)
    kiez = Application.objects.get(company__name="Kiezbank")
    assert kiez.changes.get(to_stage=Stage.REJECTED).backfilled
    stats = services.pipeline_stats(user)
    assert stats.responded == 1 and stats.median_days_to_response is None


def test_import_needs_company_and_role_columns(user):
    result = services.import_csv(user, "Employer,Salary\nKiezbank,lots\n")
    assert result.created == 0 and "company and a role" in result.errors[0]


def test_import_handles_bom_and_bad_dates(user):
    result = services.import_csv(user, "﻿Company,Role,Applied\nKiezbank,Dev,yesterday\n")
    assert result.created == 0 and "unrecognised date" in result.errors[0]


def test_import_semicolon_csv_from_german_excel(user):
    text = "Firma;Company;Role;Status;Date applied\n;Kiezbank;Backend Engineer;Applied;14.10.2026\n"
    result = services.import_csv(user, text)
    assert result.created == 1 and result.errors == []
    assert Application.objects.get().applied_on == datetime.date(2026, 10, 14)


def test_deleting_a_user_removes_their_data_but_companies_stay_protected(make_app, user):
    from django.db.models import RestrictedError

    app = make_app()
    with pytest.raises(RestrictedError):
        app.company.delete()
    user.delete()
    assert not Application.objects.exists() and not Company.objects.exists()


RICH_CSV = """Company,Role,Status,Reached,Date applied,Response date
IBM,Consultant,Rejected,Screening,01.05.2026,29.05.2026
Amazon,Ops Supervisor,Rejected,Interview,,26.05.2026
Young Engineers,Instructor,Accepted,,21.08.2026,24.08.2026
Kiezbank,Backend Engineer,Applied,,30.10.2026,
Brightgrid,Dev,Rejected,,10.10.2026,15.10.2026
"""


def test_import_reached_stage_and_response_date_feed_the_stats(user):
    result = services.import_csv(user, RICH_CSV)
    assert result.created == 5 and result.errors == []
    ibm = Application.objects.get(company__name="IBM")
    assert list(ibm.changes.values_list("to_stage", flat=True)) == ["saved", "applied", "screening", "rejected"]
    assert not ibm.changes.get(to_stage="rejected").backfilled
    young = Application.objects.get(company__name="Young Engineers")
    assert list(young.changes.values_list("to_stage", flat=True)) == ["saved", "applied", "offer", "accepted"]
    stats = services.pipeline_stats(user)
    # All five went out, including Amazon whose applied date is unknown.
    assert stats.applied == 5 and stats.offers == 1 and stats.interviewing == 1
    # Rejections at 28 and 5 days count as replies with real dates.
    assert stats.median_days_to_response == 16.5


def test_import_sets_follow_up_for_waiting_applications(user):
    services.import_csv(user, RICH_CSV)
    kiez = Application.objects.get(company__name="Kiezbank")
    assert kiez.next_follow_up == datetime.date(2026, 11, 6)
    assert Application.objects.get(company__name="IBM").next_follow_up is None


def test_import_rejects_response_before_application(user):
    result = services.import_csv(user, "Company,Role,Date applied,Response date\nX,Y,10.10.2026,01.10.2026\n")
    assert result.created == 0 and "before the applied date" in result.errors[0]


def test_import_replace_swaps_out_only_this_users_data(make_app, user, other_user):
    make_app(company="Old Co")
    make_app(company="Theirs", owner=other_user)
    result = services.import_csv(user, RICH_CSV, replace=True)
    assert result.replaced == 1 and result.created == 5
    assert not Application.objects.filter(owner=user, company__name="Old Co").exists()
    assert not Company.objects.filter(owner=user, name="Old Co").exists()
    assert Application.objects.filter(owner=other_user).count() == 1


def test_import_replace_with_unreadable_file_keeps_existing_data(make_app, user):
    make_app(company="Keep me")
    result = services.import_csv(user, "nonsense\n1\n", replace=True)
    assert result.errors and Application.objects.filter(owner=user).count() == 1


def test_withdrawn_before_applying_does_not_count_as_applied(make_app, user):
    services.move_to_stage(make_app(stage=Stage.SAVED), Stage.WITHDRAWN)
    assert services.pipeline_stats(user).applied == 0


def test_import_without_applied_date_is_dated_by_the_reply(user):
    services.import_csv(user, "Company,Role,Status,Response date\nWempe,Dev,Rejected,04.06.2026\n")
    app = Application.objects.get()
    assert {c.changed_at.date() for c in app.changes.all()} == {datetime.date(2026, 6, 4)}
