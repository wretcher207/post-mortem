"""Anthropic and Anthropic-compatible diagnosis adapter."""

import json
import os
from typing import Any, Mapping
from urllib.parse import urlparse

import anthropic
from pydantic import BaseModel, ValidationError

from .. import config
from .base import (
    ModelProfile,
    ProviderError,
    ProviderErrorCategory,
    TextDiagnosisResult,
)


SECRETS_DIR = os.path.expanduser("~/.config/david-secrets")


def _extract_single_json_object(text):
    """Return the one top-level JSON object embedded in provider text.

    Providers without native structured output sometimes wrap valid JSON in a
    short preamble or Markdown fence. Scan with the standard JSON decoder so
    nothing is evaluated, and reject zero or multiple objects rather than
    guessing which one the caller should trust.
    """
    decoder = json.JSONDecoder()
    objects = []
    offset = 0
    while offset < len(text):
        start = text.find("{", offset)
        if start < 0:
            break
        try:
            value, end = decoder.raw_decode(text, start)
        except json.JSONDecodeError:
            offset = start + 1
            continue
        if isinstance(value, dict):
            objects.append(value)
        offset = end
    if len(objects) != 1:
        raise ValueError("expected exactly one top-level JSON object")
    return objects[0]


def _first_line_matching(path, predicate):
    try:
        with open(path) as file:
            return next((line.strip() for line in file if predicate(line)), None)
    except OSError:
        return None


def _is_anthropic_endpoint(base_url):
    if not base_url:
        return True
    hostname = (urlparse(base_url).hostname or "").lower()
    return hostname == "anthropic.com" or hostname.endswith(".anthropic.com")


def _is_minimax_endpoint(base_url):
    hostname = (urlparse(base_url).hostname or "").lower()
    return hostname == "minimax.io" or hostname.endswith(".minimax.io")


def _thinking_enabled():
    mode = (config.get("POSTMORTEM_THINKING") or "adaptive").strip().lower()
    return mode not in {"off", "0", "false", "none"}


class AnthropicProvider:
    """Translate the provider-independent request into Anthropic SDK calls."""

    def __init__(self, client):
        self._client = client

    def _request_text(self, request):
        """Execute one provider request and return complete text content."""
        try:
            response = self._client.messages.create(**request)
        except (anthropic.AuthenticationError, anthropic.PermissionDeniedError) as error:
            raise ProviderError(
                ProviderErrorCategory.AUTHENTICATION,
                "provider authentication or configuration failed",
            ) from error
        except anthropic.RateLimitError as error:
            raise ProviderError(
                ProviderErrorCategory.RATE_LIMIT,
                "the provider rate limit or available credit was exhausted",
            ) from error
        except (anthropic.APITimeoutError, anthropic.APIConnectionError) as error:
            raise ProviderError(
                ProviderErrorCategory.NETWORK,
                "the provider request timed out or could not connect",
            ) from error
        except anthropic.APIStatusError as error:
            status = getattr(error, "status_code", None)
            detail = str(error).lower()
            if status in {401, 403}:
                category = ProviderErrorCategory.AUTHENTICATION
                message = "provider authentication or configuration failed"
            elif status in {402, 429} or any(
                marker in detail for marker in ("credit", "quota", "rate limit")
            ):
                category = ProviderErrorCategory.RATE_LIMIT
                message = "the provider rate limit or available credit was exhausted"
            elif status in {400, 404}:
                category = ProviderErrorCategory.AUTHENTICATION
                message = "the provider rejected the model or request configuration"
            else:
                category = ProviderErrorCategory.NETWORK
                message = f"the provider request failed with status {status or 'unknown'}"
            raise ProviderError(category, message) from error
        if response.stop_reason == "refusal":
            raise ProviderError(
                ProviderErrorCategory.REFUSAL,
                "the provider declined this diagnosis request",
            )
        text = "\n".join(
            block.text
            for block in response.content
            if block.type == "text" and block.text
        ).strip()
        if response.stop_reason == "max_tokens":
            raise ProviderError(
                ProviderErrorCategory.INCOMPLETE_RESPONSE,
                "the provider stopped before completing the diagnosis",
            )
        if not text:
            raise ProviderError(
                ProviderErrorCategory.INCOMPLETE_RESPONSE,
                "the provider returned no diagnosis content",
            )
        return text

    @staticmethod
    def _create_client(**kwargs):
        try:
            return anthropic.Anthropic(**kwargs)
        except Exception as error:
            raise ProviderError(
                ProviderErrorCategory.AUTHENTICATION,
                "provider client configuration is invalid",
            ) from error

    @staticmethod
    def model_profile_from_config(default_model="claude-opus-4-8"):
        return ModelProfile(
            model=config.get("POSTMORTEM_MODEL") or default_model,
            thinking=_thinking_enabled(),
        )

    @classmethod
    def from_config(cls):
        """Resolve endpoint, key, and model as one same-source profile."""
        base_url = config.get("ANTHROPIC_BASE_URL")

        if not _is_anthropic_endpoint(base_url):
            key = os.environ.get("POSTMORTEM_API_KEY") or config.file_get(
                "POSTMORTEM_API_KEY"
            )
            if not key and config.file_get("ANTHROPIC_BASE_URL") == base_url:
                key = config.file_get("ANTHROPIC_API_KEY")
            if key:
                client = cls._create_client(api_key=key, base_url=base_url)
                profile = cls.model_profile_from_config()
                return cls(client), profile

            key_line = _first_line_matching(
                os.path.join(SECRETS_DIR, "minimax-api.md"),
                lambda line: line.startswith("- Key:"),
            )
            if key_line and _is_minimax_endpoint(base_url):
                client = cls._create_client(
                    api_key=key_line.split(":", 1)[1].strip(), base_url=base_url
                )
                profile = cls.model_profile_from_config(default_model="MiniMax-M3")
                return cls(client), profile

            raise ProviderError(
                ProviderErrorCategory.AUTHENTICATION,
                f"{base_url} is a third-party endpoint and requires its own "
                "POSTMORTEM_API_KEY; the Anthropic environment key was not sent",
            )

        key = config.get("ANTHROPIC_API_KEY") or _first_line_matching(
            os.path.join(SECRETS_DIR, "anthropic-api-key"),
            lambda line: line.startswith("sk-ant-"),
        )
        if not key:
            raise ProviderError(
                ProviderErrorCategory.AUTHENTICATION,
                f"no Anthropic API key found; set ANTHROPIC_API_KEY or add it to "
                f"{config.CONFIG_PATH}",
            )
        client = cls._create_client(api_key=key)
        profile = cls.model_profile_from_config()
        return cls(client), profile

    def generate(
        self,
        *,
        system_contract: str,
        payload: Mapping[str, Any],
        response_schema: type[BaseModel],
        model_profile: ModelProfile,
        user_instruction: str,
    ) -> dict[str, Any]:
        instruction = user_instruction
        if response_schema is not TextDiagnosisResult:
            instruction += (
                "\nReturn JSON only: exactly one top-level object matching this "
                "schema. Do not include Markdown fences or commentary.\nSCHEMA:\n"
                + json.dumps(response_schema.model_json_schema(), separators=(",", ":"))
            )
        request = {
            "model": model_profile.model,
            "max_tokens": model_profile.max_tokens,
            "system": system_contract,
            "messages": [
                {
                    "role": "user",
                    "content": f"{instruction}\n\nPAYLOAD:\n"
                    + json.dumps(payload, indent=1),
                }
            ],
        }
        if model_profile.thinking:
            request["thinking"] = {"type": "adaptive"}

        text = self._request_text(request)
        try:
            if response_schema is TextDiagnosisResult:
                validated = response_schema.model_validate({"text": text})
            else:
                validated = response_schema.model_validate(
                    _extract_single_json_object(text)
                )
        except (ValidationError, ValueError) as error:
            if response_schema is TextDiagnosisResult:
                raise ProviderError(
                    ProviderErrorCategory.INVALID_RESPONSE,
                    "the provider returned content that did not match the target schema",
                ) from error
            repair_request = {
                "model": model_profile.model,
                "max_tokens": model_profile.max_tokens,
                "system": system_contract,
                "messages": [
                    {
                        "role": "user",
                        "content": (
                            "Repair the response below into exactly one JSON object "
                            "matching the supplied schema. Return JSON only.\n\n"
                            f"SCHEMA:\n{json.dumps(response_schema.model_json_schema())}"
                            f"\n\nRESPONSE:\n{text}"
                        ),
                    }
                ],
            }
            if model_profile.thinking:
                repair_request["thinking"] = {"type": "adaptive"}
            repaired_text = self._request_text(repair_request)
            try:
                validated = response_schema.model_validate(
                    _extract_single_json_object(repaired_text)
                )
            except (ValidationError, ValueError) as repair_error:
                raise ProviderError(
                    ProviderErrorCategory.INVALID_RESPONSE,
                    "the provider returned content that did not match the target schema",
                ) from repair_error
        return validated.model_dump(mode="json")
