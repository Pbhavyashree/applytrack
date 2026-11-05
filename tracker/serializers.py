from rest_framework import serializers

from .models import Application, Company, Interview, Stage, StageChange


class CompanySerializer(serializers.ModelSerializer):
    class Meta:
        model = Company
        fields = ["id", "name", "website", "notes"]

    def validate_name(self, value):
        owner = self.context["request"].user
        clash = Company.objects.filter(owner=owner, name__iexact=value.strip())
        if self.instance:
            clash = clash.exclude(pk=self.instance.pk)
        if clash.exists():
            raise serializers.ValidationError("You already have a company with this name.")
        return value.strip()


class StageChangeSerializer(serializers.ModelSerializer):
    class Meta:
        model = StageChange
        fields = ["from_stage", "to_stage", "changed_at", "note", "backfilled"]


class InterviewSerializer(serializers.ModelSerializer):
    class Meta:
        model = Interview
        fields = ["id", "application", "kind", "scheduled_at", "with_whom", "notes"]

    def validate_application(self, app):
        if app.owner_id != self.context["request"].user.id:
            raise serializers.ValidationError("Application not found.")
        return app


class ApplicationSerializer(serializers.ModelSerializer):
    """Reads return the company name and current stage; creates take a
    company *name* (found or created) and may start as saved or applied."""

    company = serializers.CharField(source="company.name", read_only=True)
    company_name = serializers.CharField(write_only=True, max_length=120)
    stage = serializers.ChoiceField(choices=[Stage.SAVED, Stage.APPLIED], default=Stage.SAVED)
    stage_display = serializers.CharField(source="get_stage_display", read_only=True)
    allowed_next_stages = serializers.ListField(child=serializers.CharField(), read_only=True)

    class Meta:
        model = Application
        fields = ["id", "company", "company_name", "role", "job_url", "location", "source", "stage",
                  "stage_display", "allowed_next_stages", "applied_on", "next_follow_up", "notes",
                  "created_at", "updated_at"]
        read_only_fields = ["created_at", "updated_at"]

    def get_fields(self):
        fields = super().get_fields()
        if self.instance is not None:
            # On update, stage changes only via the /move/ action and the
            # company is fixed; other fields stay editable.
            fields["stage"].read_only = True
            fields["company_name"].required = False
        return fields

    def create(self, validated_data):
        from .services import create_application

        return create_application(
            self.context["request"].user,
            company_name=validated_data.pop("company_name"),
            role=validated_data.pop("role"),
            stage=validated_data.pop("stage", Stage.SAVED),
            **validated_data,
        )

    def update(self, instance, validated_data):
        validated_data.pop("company_name", None)
        validated_data.pop("stage", None)
        return super().update(instance, validated_data)


class ApplicationDetailSerializer(ApplicationSerializer):
    history = StageChangeSerializer(source="changes", many=True, read_only=True)
    interviews = InterviewSerializer(many=True, read_only=True)

    class Meta(ApplicationSerializer.Meta):
        fields = ApplicationSerializer.Meta.fields + ["history", "interviews"]


class MoveSerializer(serializers.Serializer):
    stage = serializers.ChoiceField(choices=Stage.choices)
    note = serializers.CharField(required=False, allow_blank=True, max_length=300)
    on = serializers.DateField(required=False, help_text="Date applied, when moving to applied")


class ImportSerializer(serializers.Serializer):
    file = serializers.FileField()


class SourceStatsSerializer(serializers.Serializer):
    source = serializers.CharField()
    label = serializers.CharField()
    applied = serializers.IntegerField()
    responded = serializers.IntegerField()
    response_rate = serializers.FloatField(allow_null=True)


class StatsSerializer(serializers.Serializer):
    applied = serializers.IntegerField()
    responded = serializers.IntegerField()
    interviewing = serializers.IntegerField()
    offers = serializers.IntegerField()
    active = serializers.IntegerField()
    response_rate = serializers.FloatField(allow_null=True)
    interview_rate = serializers.FloatField(allow_null=True)
    median_days_to_response = serializers.FloatField(allow_null=True)
    by_stage = serializers.DictField(child=serializers.IntegerField())
    by_source = SourceStatsSerializer(many=True)
