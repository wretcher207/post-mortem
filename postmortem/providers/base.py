"""Provider-independent diagnosis interface and shared contract types."""

from dataclasses import dataclass
from enum import Enum
from typing import Any, Mapping, Protocol

from pydantic import BaseModel, ConfigDict, Field


class ProviderErrorCategory(str, Enum):
    AUTHENTICATION = "authentication"
    RATE_LIMIT = "rate_limit"
    NETWORK = "network"
    REFUSAL = "refusal"
    INCOMPLETE_RESPONSE = "incomplete_response"
    INVALID_RESPONSE = "invalid_response"


_PROVIDER_EXIT_CODES = {
    ProviderErrorCategory.AUTHENTICATION: 4,
    ProviderErrorCategory.RATE_LIMIT: 5,
    ProviderErrorCategory.NETWORK: 6,
    ProviderErrorCategory.REFUSAL: 7,
    ProviderErrorCategory.INCOMPLETE_RESPONSE: 8,
    ProviderErrorCategory.INVALID_RESPONSE: 9,
}


class ProviderError(RuntimeError):
    """Typed provider failure safe to surface through the CLI."""

    def __init__(self, category: ProviderErrorCategory, message: str):
        super().__init__(message)
        self.category = category
        self.exit_code = _PROVIDER_EXIT_CODES[category]


@dataclass(frozen=True)
class ModelProfile:
    """Model-specific request settings selected outside diagnosis orchestration."""

    model: str
    max_tokens: int = 16_384
    thinking: bool = True


class TextDiagnosisResult(BaseModel):
    """Compatibility schema for the current prose diagnosis path."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    text: str = Field(min_length=1)


class DiagnosisProvider(Protocol):
    """A provider adapter that returns schema-validated JSON-compatible data."""

    def generate(
        self,
        *,
        system_contract: str,
        payload: Mapping[str, Any],
        response_schema: type[BaseModel],
        model_profile: ModelProfile,
        user_instruction: str,
    ) -> dict[str, Any]: ...
