import datetime

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db.models import Prefetch, Q
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST
from rest_framework import mixins, parsers, permissions, status, viewsets
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.views import APIView

from . import services
from .forms import ApplicationForm, EditApplicationForm, ImportForm, InterviewForm, MoveForm
from .models import ACTIVE_STAGES, CLOSED_STAGES, Application, Company, Interview, Stage
from .serializers import (
    ApplicationDetailSerializer,
    ApplicationSerializer,
    CompanySerializer,
    ImportSerializer,
    InterviewSerializer,
    MoveSerializer,
    StatsSerializer,
)

# Every queryset below is filtered by request.user. Another user's rows
# return 404, never 403, so their existence isn't revealed.


# --- API ------------------------------------------------------------------


class ApplicationViewSet(viewsets.ModelViewSet):
    permission_classes = [permissions.IsAuthenticated]

    def get_serializer_class(self):
        return ApplicationDetailSerializer if self.action == "retrieve" else ApplicationSerializer

    def get_queryset(self):
        qs = Application.objects.filter(owner=self.request.user).select_related("company")
        if self.action == "retrieve":
            qs = qs.prefetch_related("changes", "interviews")
        params = self.request.query_params
        if stage := params.get("stage"):
            qs = qs.filter(stage__in=stage.split(","))
        if source := params.get("source"):
            qs = qs.filter(source=source)
        if params.get("active") in {"1", "true"}:
            qs = qs.filter(stage__in=ACTIVE_STAGES)
        if q := params.get("q"):
            qs = qs.filter(Q(role__icontains=q) | Q(company__name__icontains=q))
        return qs

    @action(detail=True, methods=["post"])
    def move(self, request, pk=None):
        """{"stage": "interview", "note": "Tech round booked"}"""
        payload = MoveSerializer(data=request.data)
        payload.is_valid(raise_exception=True)
        try:
            data = payload.validated_data
            app = services.move_to_stage(self.get_object(), data["stage"], note=data.get("note", ""), on=data.get("on"))
        except services.TransitionError as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_409_CONFLICT)
        return Response(ApplicationDetailSerializer(app, context={"request": request}).data)

    @action(detail=False, methods=["get"], url_path="follow-ups")
    def follow_ups(self, request):
        apps = services.follow_ups_due(request.user)
        return Response(ApplicationSerializer(apps, many=True, context={"request": request}).data)

    @action(detail=False, methods=["post"], url_path="import",
            parser_classes=[parsers.MultiPartParser])
    def import_csv(self, request):
        payload = ImportSerializer(data=request.data)
        payload.is_valid(raise_exception=True)
        text = payload.validated_data["file"].read().decode("utf-8-sig", errors="replace")
        result = services.import_csv(request.user, text)
        return Response({"created": result.created, "skipped": result.skipped, "errors": result.errors})


class CompanyViewSet(viewsets.ModelViewSet):
    serializer_class = CompanySerializer
    permission_classes = [permissions.IsAuthenticated]

    def get_queryset(self):
        return Company.objects.filter(owner=self.request.user)

    def perform_create(self, serializer):
        serializer.save(owner=self.request.user)

    def destroy(self, request, *args, **kwargs):
        if self.get_object().applications.exists():
            return Response({"detail": "Delete or move this company's applications first."},
                            status=status.HTTP_409_CONFLICT)
        return super().destroy(request, *args, **kwargs)


class InterviewViewSet(mixins.CreateModelMixin, mixins.UpdateModelMixin, mixins.DestroyModelMixin,
                       mixins.ListModelMixin, viewsets.GenericViewSet):
    serializer_class = InterviewSerializer
    permission_classes = [permissions.IsAuthenticated]

    def get_queryset(self):
        qs = Interview.objects.filter(application__owner=self.request.user)
        if self.request.query_params.get("upcoming") in {"1", "true"}:
            qs = qs.filter(scheduled_at__gte=timezone.now())
        return qs


class StatsView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request):
        since = request.query_params.get("since")
        try:
            since_date = datetime.date.fromisoformat(since) if since else None
        except ValueError:
            return Response({"since": "Use YYYY-MM-DD."}, status=status.HTTP_400_BAD_REQUEST)
        stats = services.pipeline_stats(request.user, since=since_date)
        return Response(StatsSerializer(stats).data)


# --- Web pages ------------------------------------------------------------


def _own_app(request, pk):
    return get_object_or_404(Application.objects.select_related("company"), pk=pk, owner=request.user)


@login_required
def dashboard(request):
    apps = Application.objects.filter(owner=request.user).select_related("company")
    columns = [
        {"stage": stage, "label": Stage(stage).label, "apps": [a for a in apps if a.stage == stage]}
        for stage in ACTIVE_STAGES
    ]
    upcoming = (
        Interview.objects.filter(application__owner=request.user, scheduled_at__gte=timezone.now())
        .select_related("application__company")[:5]
    )
    return render(request, "tracker/dashboard.html", {
        "columns": columns,
        "closed_count": sum(1 for a in apps if a.stage in CLOSED_STAGES),
        "follow_ups": services.follow_ups_due(request.user),
        "upcoming": upcoming,
        "stats": services.pipeline_stats(request.user),
        "today": timezone.localdate(),
    })


@login_required
def closed(request):
    apps = (Application.objects.filter(owner=request.user, stage__in=CLOSED_STAGES)
            .select_related("company").order_by("-updated_at"))
    return render(request, "tracker/closed.html", {"apps": apps})


@login_required
def add_application(request):
    form = ApplicationForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        data = form.cleaned_data
        app = services.create_application(
            request.user,
            company_name=data.pop("company_name"),
            role=data.pop("role"),
            stage=data.pop("stage"),
            **data,
        )
        messages.success(request, f"Added {app}.")
        return redirect("detail", pk=app.pk)
    return render(request, "tracker/form.html", {"form": form, "title": "Add application"})


@login_required
def detail(request, pk):
    app = get_object_or_404(
        Application.objects.select_related("company").prefetch_related(
            "changes", Prefetch("interviews", queryset=Interview.objects.order_by("scheduled_at"))
        ),
        pk=pk, owner=request.user,
    )
    edit_form = EditApplicationForm(request.POST or None, instance=app, prefix="edit")
    if request.method == "POST" and edit_form.is_valid():
        edit_form.save()
        messages.success(request, "Saved.")
        return redirect("detail", pk=app.pk)
    return render(request, "tracker/detail.html", {
        "app": app,
        "edit_form": edit_form,
        "interview_form": InterviewForm(prefix="iv"),
        "next_stages": [(s, Stage(s).label) for s in app.allowed_next_stages()],
    })


@login_required
@require_POST
def move(request, pk):
    app = _own_app(request, pk)
    form = MoveForm(request.POST)
    if form.is_valid():
        try:
            services.move_to_stage(app, form.cleaned_data["stage"], note=form.cleaned_data["note"])
            messages.success(request, f"Moved to {Stage(form.cleaned_data['stage']).label}.")
        except services.TransitionError as exc:
            messages.error(request, str(exc))
    return redirect("detail", pk=app.pk)


@login_required
@require_POST
def add_interview(request, pk):
    app = _own_app(request, pk)
    form = InterviewForm(request.POST, prefix="iv")
    if form.is_valid():
        interview = form.save(commit=False)
        interview.application = app
        interview.save()
        if app.stage in (Stage.APPLIED, Stage.SCREENING, Stage.GHOSTED):
            services.move_to_stage(app, Stage.INTERVIEW, note=f"{interview.get_kind_display()} booked")
        messages.success(request, "Interview added.")
    else:
        messages.error(request, "Check the interview details and try again.")
    return redirect("detail", pk=app.pk)


@login_required
def import_view(request):
    form = ImportForm(request.POST or None, request.FILES or None)
    result = None
    if request.method == "POST" and form.is_valid():
        result = services.import_csv(request.user, form.cleaned_data["file"])
    return render(request, "tracker/import.html", {"form": form, "result": result})
