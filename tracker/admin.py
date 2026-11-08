import csv

from django.contrib import admin, messages
from django.db.models import Count, Q
from django.http import HttpResponse

from . import services
from .models import ACTIVE_STAGES, Application, Company, Interview, Stage, StageChange, UserSettings

admin.site.site_header = "Applytrack"
admin.site.site_title = "Applytrack admin"
admin.site.index_title = "Job search"


class OwnedAdmin(admin.ModelAdmin):
    """Superusers see everything; anyone else sees only their own rows."""

    owner_path = "owner"

    def get_queryset(self, request):
        qs = super().get_queryset(request)
        return qs if request.user.is_superuser else qs.filter(**{self.owner_path: request.user})


class StageChangeInline(admin.TabularInline):
    model = StageChange
    extra = 0
    can_delete = False
    fields = ["changed_at", "from_stage", "to_stage", "note", "backfilled"]
    readonly_fields = fields

    def has_add_permission(self, request, obj=None):
        return False


class InterviewInline(admin.TabularInline):
    model = Interview
    extra = 0
    fields = ["kind", "scheduled_at", "with_whom", "notes"]


@admin.register(Company)
class CompanyAdmin(OwnedAdmin):
    list_display = ["name", "application_count", "active_count", "website"]
    search_fields = ["name"]

    def get_queryset(self, request):
        return super().get_queryset(request).annotate(
            _apps=Count("applications"),
            _active=Count("applications", filter=Q(applications__stage__in=ACTIVE_STAGES)),
        )

    @admin.display(description="Applications", ordering="_apps")
    def application_count(self, obj):
        return obj._apps

    @admin.display(description="Active", ordering="_active")
    def active_count(self, obj):
        return obj._active

    def save_model(self, request, obj, form, change):
        if not change:
            obj.owner = request.user
        super().save_model(request, obj, form, change)

    def get_exclude(self, request, obj=None):
        return ["owner"]


@admin.register(Application)
class ApplicationAdmin(OwnedAdmin):
    list_display = ["role", "company", "stage", "source", "applied_on", "next_follow_up", "updated_at"]
    list_filter = ["stage", "source", ("applied_on", admin.DateFieldListFilter)]
    search_fields = ["role", "company__name", "notes"]
    date_hierarchy = "applied_on"
    list_select_related = ["company"]
    readonly_fields = ["stage", "created_at", "updated_at"]
    exclude = ["owner"]
    inlines = [InterviewInline, StageChangeInline]
    actions = ["mark_rejected", "mark_ghosted", "export_csv"]

    def formfield_for_foreignkey(self, db_field, request, **kwargs):
        if db_field.name == "company" and not request.user.is_superuser:
            kwargs["queryset"] = Company.objects.filter(owner=request.user)
        return super().formfield_for_foreignkey(db_field, request, **kwargs)

    def save_model(self, request, obj, form, change):
        # The stage field is read-only here: moves go through the service so
        # they're validated and recorded. A new row starts as Saved.
        if not change:
            obj.owner = request.user
        super().save_model(request, obj, form, change)
        if not change:
            StageChange.objects.create(application=obj, to_stage=Stage.SAVED, changed_at=obj.created_at)

    def _bulk_move(self, request, queryset, stage):
        moved, refused = 0, 0
        for app in queryset:
            try:
                services.move_to_stage(app, stage, note="Bulk update in admin")
                moved += 1
            except services.TransitionError:
                refused += 1
        self.message_user(request, f"Moved {moved}.", messages.SUCCESS)
        if refused:
            self.message_user(request, f"{refused} couldn't move from their current stage.", messages.WARNING)

    @admin.action(description="Mark selected as rejected")
    def mark_rejected(self, request, queryset):
        self._bulk_move(request, queryset, Stage.REJECTED)

    @admin.action(description="Mark selected as no reply")
    def mark_ghosted(self, request, queryset):
        self._bulk_move(request, queryset, Stage.GHOSTED)

    @admin.action(description="Export selected as CSV")
    def export_csv(self, request, queryset):
        response = HttpResponse(content_type="text/csv")
        response["Content-Disposition"] = 'attachment; filename="applications.csv"'
        writer = csv.writer(response)
        writer.writerow(["company", "role", "stage", "source", "applied_on", "location", "url", "notes"])
        for a in queryset.select_related("company"):
            writer.writerow([a.company.name, a.role, a.get_stage_display(), a.get_source_display(),
                             a.applied_on or "", a.location, a.job_url, a.notes])
        return response


@admin.register(Interview)
class InterviewAdmin(OwnedAdmin):
    owner_path = "application__owner"
    list_display = ["application", "kind", "scheduled_at", "with_whom"]
    list_filter = ["kind"]
    list_select_related = ["application__company"]
    date_hierarchy = "scheduled_at"
    raw_id_fields = ["application"]


@admin.register(UserSettings)
class UserSettingsAdmin(OwnedAdmin):
    owner_path = "user"
    list_display = ["user", "theme"]
