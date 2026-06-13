"""Types for the onboarding LLM contracts and commit payload."""

from dataclasses import dataclass, field

from vital_types.db import (
    DailyLogSchemaField,
    PresenceConfig,
    ProfileInput,
    ScheduledJob,
)


@dataclass
class FollowUpQuestion:
    """One adaptive follow-up question from onboarding Call 1."""
    question_id: str
    question: str
    type: str
    reason: str


@dataclass
class MealPlanFramework:
    """Nutrition framework generated at onboarding."""
    nutrients_to_prioritise: list[str]
    nutrients_to_moderate: list[str]
    meal_frequency: int
    notes: str


@dataclass
class ExercisePlan:
    """Exercise plan generated at onboarding."""
    frequency: str
    intensity: str
    session_duration_minutes: int
    types: list[str]
    avoid: list[str]
    notes: str


@dataclass
class WeeklyCheckStructure:
    """Weekly report and replan schedule from onboarding."""
    report_day: str
    report_time: str
    replan_day: str
    replan_time: str


@dataclass
class OnboardingPlan:
    """Parsed and validated plan from onboarding Call 2."""
    daily_log_fields: list[DailyLogSchemaField]
    scheduled_jobs: list[ScheduledJob]
    meal_plan_framework: MealPlanFramework
    exercise_plan: ExercisePlan
    weekly_check_structure: WeeklyCheckStructure
    presence_check: PresenceConfig
    system_prompt_additions: str
    hydration_goal_liters: float = 2.5
    coach_quick_questions: list[str] = field(default_factory=list)


@dataclass
class OnboardingCommitData:
    """Everything written to the database on onboarding approval."""
    profile: ProfileInput
    plan: OnboardingPlan
