"""Validated, provider-independent diagnosis result contract."""

from typing import Annotated, Literal, TypeAlias

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StrictBool,
    StrictInt,
    model_validator,
)


Confidence: TypeAlias = Literal["low", "medium", "high"]
ProposalOperation: TypeAlias = Literal[
    "none",
    "set_track_volume",
    "set_track_pan",
    "set_fx_param",
    "set_fx_bypass",
]
MetricDirection: TypeAlias = Literal[
    "increase",
    "decrease",
    "not_increase",
    "not_decrease",
    "unchanged",
]
ValueUnit: TypeAlias = Literal["db", "normalized_pan", "normalized", "boolean"]
SupportedMetric: TypeAlias = Literal[
    "sample_peak_db",
    "true_peak_db",
    "rms_db",
    "crest_factor_db",
    "integrated_lufs",
    "loudness_range_lu",
    "lufs_momentary_max",
    "lufs_short_term_max",
    "silence_fraction",
    "stereo_correlation",
    "stereo_balance_db",
    "mid_rms_db",
    "side_rms_db",
    "spectrum_third_octave",
]
StrictFiniteFloat: TypeAlias = Annotated[
    float, Field(strict=True, allow_inf_nan=False)
]


class _ContractModel(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class EvidenceRef(_ContractModel):
    path: str = Field(
        min_length=1,
        max_length=256,
        description=(
            "An exact leaf path from the supplied payload, such as "
            "audio.sample_peak_db or fx_chain[0].enabled; the referenced value "
            "must exist and must not be null."
        ),
    )
    description: str | None = Field(default=None, max_length=500)


class Finding(_ContractModel):
    summary: str = Field(min_length=1, max_length=1_000)
    probable_cause: str = Field(min_length=1, max_length=1_000)
    confidence: Confidence
    confidence_reason: str = Field(min_length=1, max_length=500)
    evidence_refs: list[EvidenceRef] = Field(max_length=20)


class ExpectedMetricDirection(_ContractModel):
    metric: str = Field(min_length=1, max_length=100)
    direction: MetricDirection


class ProposalTarget(_ContractModel):
    track_guid: str = Field(min_length=1, max_length=256)
    track_name: str | None = Field(default=None, min_length=1, max_length=256)
    fx_guid: str | None = Field(default=None, min_length=1, max_length=256)
    fx_index: StrictInt | None = Field(default=None, ge=0)
    fx_scope: str | None = Field(default=None, min_length=1, max_length=64)
    fx_name: str | None = Field(default=None, min_length=1, max_length=256)
    parameter_index: StrictInt | None = Field(default=None, ge=0)
    parameter_name: str | None = Field(default=None, min_length=1, max_length=256)


class ProposalValue(_ContractModel):
    value: StrictFiniteFloat | StrictBool
    unit: ValueUnit
    display: str | None = Field(default=None, max_length=256)

    @model_validator(mode="after")
    def validate_value_for_unit(self):
        if self.unit == "boolean":
            if not isinstance(self.value, bool):
                raise ValueError("boolean values must be true or false")
            return self
        if isinstance(self.value, bool):
            raise ValueError(f"{self.unit} values must be numeric")
        if self.unit == "normalized" and not 0.0 <= self.value <= 1.0:
            raise ValueError("normalized values must be within 0.0-1.0")
        if self.unit == "normalized_pan" and not -1.0 <= self.value <= 1.0:
            raise ValueError("normalized pan values must be within -1.0-1.0")
        return self


class _ProposalCore(_ContractModel):
    operation: ProposalOperation
    reason: str = Field(min_length=1, max_length=1_000)
    target: ProposalTarget | None = None
    current_value: ProposalValue | None = None
    proposed_value: ProposalValue | None = None
    goal: str | None = Field(default=None, max_length=100)
    expected_direction: list[ExpectedMetricDirection] = Field(max_length=10)

    @model_validator(mode="after")
    def validate_operation_shape(self):
        if self.operation == "none" and (
            self.target is not None or self.proposed_value is not None
        ):
            raise ValueError("none proposal cannot carry action fields")
        if self.operation != "none" and (
            any(
                value is None
                for value in (
                    self.target,
                    self.current_value,
                    self.proposed_value,
                    self.goal,
                )
            )
            or not self.expected_direction
        ):
            raise ValueError(
                "actionable proposal requires target, current and proposed values, "
                "goal, and expected metric directions"
            )
        required_unit = {
            "set_track_volume": "db",
            "set_track_pan": "normalized_pan",
            "set_fx_param": "normalized",
            "set_fx_bypass": "boolean",
        }.get(self.operation)
        if required_unit and self.current_value and self.proposed_value:
            if (
                self.current_value.unit != required_unit
                or self.proposed_value.unit != required_unit
            ):
                raise ValueError(
                    f"{self.operation} values must use {required_unit} units"
                )
        if self.operation in {"set_fx_param", "set_fx_bypass"} and self.target:
            if any(
                value is None
                for value in (
                    self.target.fx_guid,
                    self.target.fx_index,
                    self.target.fx_scope,
                    self.target.fx_name,
                )
            ):
                raise ValueError(
                    "FX operations require FX GUID, index, scope, and verified name"
                )
        if self.operation == "set_fx_param" and self.target:
            if (
                self.target.parameter_index is None
                or self.target.parameter_name is None
            ):
                raise ValueError(
                    "set_fx_param requires parameter index and verified name"
                )
        return self


class ProviderExpectedMetricDirection(ExpectedMetricDirection):
    """Metric direction accepted directly from a model provider."""

    metric: SupportedMetric


class ProviderProposal(_ProposalCore):
    """Model-authored proposal fields; rejection state belongs to validators."""

    goal: SupportedMetric | None = None
    expected_direction: list[ProviderExpectedMetricDirection] = Field(max_length=10)


class Proposal(_ProposalCore):
    rejection_reason: str | None = Field(default=None, max_length=100)


class DiagnosisResult(_ContractModel):
    schema_version: StrictInt = Field(ge=1, le=1)
    finding: Finding
    proposal: Proposal


class ProviderDiagnosisResult(_ContractModel):
    """Strict model-facing result converted into the public diagnosis contract."""

    schema_version: StrictInt = Field(ge=1, le=1)
    finding: Finding
    proposal: ProviderProposal
