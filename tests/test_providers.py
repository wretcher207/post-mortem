"""Behavioral tests for the provider-independent diagnosis seam."""

import subprocess
import sys
from unittest.mock import patch

import anthropic
import httpx
import pytest
from pydantic import BaseModel, ConfigDict

from postmortem import config, diagnose
from postmortem.providers.anthropic_provider import AnthropicProvider
from postmortem.providers.base import (
    ModelProfile,
    ProviderError,
    ProviderErrorCategory,
    TextDiagnosisResult,
)


class _TestProvider:
    def __init__(self):
        self.calls = []

    def generate(self, **kwargs):
        self.calls.append(kwargs)
        return {"text": "DIAGNOSIS: provider seam works"}


class _Block:
    def __init__(self, text=None, type="text"):
        self.type = type
        self.text = text


class _Response:
    def __init__(self, content, stop_reason="end_turn"):
        self.content = content
        self.stop_reason = stop_reason


class _FakeAnthropicClient:
    def __init__(self, response):
        self.calls = []
        self.response = response

        class _Messages:
            def create(inner, **kwargs):
                self.calls.append(kwargs)
                return self.response

        self.messages = _Messages()


class _SequenceAnthropicClient:
    def __init__(self, responses):
        self.calls = []
        self.responses = iter(responses)

        class _Messages:
            def create(inner, **kwargs):
                self.calls.append(kwargs)
                return next(self.responses)

        self.messages = _Messages()


class _RaisingAnthropicClient:
    def __init__(self, error):
        class _Messages:
            def create(inner, **kwargs):
                raise error

        self.messages = _Messages()


class _StructuredResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    finding: str


def test_diagnose_accepts_an_injected_provider_and_model_profile():
    provider = _TestProvider()
    profile = ModelProfile(model="test-model", thinking=False)

    result = diagnose.diagnose(
        {"audio": {"sample_peak_db": -3.0}},
        provider=provider,
        profile=profile,
    )

    assert result == "DIAGNOSIS: provider seam works"
    assert provider.calls[0]["response_schema"] is TextDiagnosisResult
    assert provider.calls[0]["model_profile"] == profile


@pytest.mark.parametrize(
    ("category", "exit_code"),
    [
        (ProviderErrorCategory.AUTHENTICATION, 4),
        (ProviderErrorCategory.RATE_LIMIT, 5),
        (ProviderErrorCategory.NETWORK, 6),
        (ProviderErrorCategory.REFUSAL, 7),
        (ProviderErrorCategory.INCOMPLETE_RESPONSE, 8),
        (ProviderErrorCategory.INVALID_RESPONSE, 9),
    ],
)
def test_provider_error_categories_have_stable_exit_codes(category, exit_code):
    error = ProviderError(category, "safe message")

    assert error.category is category
    assert error.exit_code == exit_code
    assert str(error) == "safe message"


def test_anthropic_adapter_returns_validated_data_without_leaking_sdk_objects():
    client = _FakeAnthropicClient(
        _Response([_Block("DIAGNOSIS: one"), _Block("CONFIDENCE: medium")])
    )
    provider = AnthropicProvider(client)

    result = provider.generate(
        system_contract="honesty contract",
        payload={"audio": {"sample_peak_db": -3.0}},
        response_schema=TextDiagnosisResult,
        model_profile=ModelProfile(model="claude-test", thinking=True),
        user_instruction="Diagnose this track:",
    )

    assert result == {"text": "DIAGNOSIS: one\nCONFIDENCE: medium"}
    assert client.calls[0]["model"] == "claude-test"
    assert client.calls[0]["system"] == "honesty contract"
    assert client.calls[0]["thinking"] == {"type": "adaptive"}


def test_structured_adapter_extracts_one_json_object_from_provider_preamble():
    provider = AnthropicProvider(
        _FakeAnthropicClient(
            _Response(
                [
                    _Block(
                        'I checked the evidence first.\n```json\n'
                        '{"finding":"high-frequency buildup"}\n```'
                    )
                ]
            )
        )
    )

    result = provider.generate(
        system_contract="contract",
        payload={},
        response_schema=_StructuredResult,
        model_profile=ModelProfile(model="test", thinking=False),
        user_instruction="Diagnose:",
    )

    assert result == {"finding": "high-frequency buildup"}


def test_structured_adapter_allows_one_schema_repair_request():
    client = _SequenceAnthropicClient(
        [
            _Response([_Block('{"wrong":"shape"}')]),
            _Response([_Block('{"finding":"controlled repair"}')]),
        ]
    )
    provider = AnthropicProvider(client)

    result = provider.generate(
        system_contract="contract",
        payload={"track": {"guid": "{TRACK-GUID}"}},
        response_schema=_StructuredResult,
        model_profile=ModelProfile(model="test", thinking=False),
        user_instruction="Diagnose:",
    )

    assert result == {"finding": "controlled repair"}
    assert len(client.calls) == 2
    repair_content = client.calls[1]["messages"][0]["content"]
    assert "repair" in repair_content.lower()
    assert "{TRACK-GUID}" in repair_content


def test_structured_adapter_requests_json_only_with_the_target_schema():
    client = _FakeAnthropicClient(
        _Response([_Block('{"finding":"schema-guided"}')])
    )
    provider = AnthropicProvider(client)

    provider.generate(
        system_contract="honesty contract",
        payload={},
        response_schema=_StructuredResult,
        model_profile=ModelProfile(model="test", thinking=False),
        user_instruction="Diagnose:",
    )

    content = client.calls[0]["messages"][0]["content"]
    assert "return json only" in content.lower()
    assert '"finding"' in content


def test_structured_adapter_stops_after_one_failed_repair():
    client = _SequenceAnthropicClient(
        [
            _Response([_Block('{"wrong":"first"}')]),
            _Response([_Block('{"wrong":"repair"}')]),
            _Response([_Block('{"finding":"must not be requested"}')]),
        ]
    )
    provider = AnthropicProvider(client)

    with pytest.raises(ProviderError) as caught:
        provider.generate(
            system_contract="contract",
            payload={},
            response_schema=_StructuredResult,
            model_profile=ModelProfile(model="test", thinking=False),
            user_instruction="Diagnose:",
        )

    assert caught.value.category is ProviderErrorCategory.INVALID_RESPONSE
    assert len(client.calls) == 2


def test_structured_adapter_maps_empty_repair_to_invalid_response():
    client = _SequenceAnthropicClient(
        [
            _Response([_Block('{"wrong":"first"}')]),
            _Response([]),
        ]
    )
    provider = AnthropicProvider(client)

    with pytest.raises(ProviderError) as caught:
        provider.generate(
            system_contract="contract",
            payload={},
            response_schema=_StructuredResult,
            model_profile=ModelProfile(model="test", thinking=False),
            user_instruction="Diagnose:",
        )

    assert caught.value.category is ProviderErrorCategory.INVALID_RESPONSE
    assert len(client.calls) == 2


def test_structured_adapter_never_chooses_between_multiple_json_objects():
    provider = AnthropicProvider(
        _FakeAnthropicClient(
            _Response(
                [_Block('{"finding":"first"}\n{"finding":"second"}')]
            )
        )
    )

    with pytest.raises(ProviderError) as caught:
        provider.generate(
            system_contract="contract",
            payload={},
            response_schema=_StructuredResult,
            model_profile=ModelProfile(model="test", thinking=False),
            user_instruction="Diagnose:",
        )

    assert caught.value.category is ProviderErrorCategory.INVALID_RESPONSE


def test_anthropic_adapter_maps_invalid_structured_output_to_typed_error():
    provider = AnthropicProvider(
        _FakeAnthropicClient(_Response([_Block('{"unexpected":"shape"}')]))
    )

    with pytest.raises(ProviderError) as caught:
        provider.generate(
            system_contract="contract",
            payload={},
            response_schema=_StructuredResult,
            model_profile=ModelProfile(model="test", thinking=False),
            user_instruction="Diagnose:",
        )

    assert caught.value.category is ProviderErrorCategory.INVALID_RESPONSE


@pytest.mark.parametrize(
    ("response", "category"),
    [
        (_Response([], stop_reason="refusal"), ProviderErrorCategory.REFUSAL),
        (_Response([]), ProviderErrorCategory.INCOMPLETE_RESPONSE),
        (
            _Response([_Block("partial")], stop_reason="max_tokens"),
            ProviderErrorCategory.INCOMPLETE_RESPONSE,
        ),
    ],
)
def test_anthropic_adapter_types_refusal_empty_and_truncated_responses(
    response, category
):
    provider = AnthropicProvider(_FakeAnthropicClient(response))

    with pytest.raises(ProviderError) as caught:
        provider.generate(
            system_contract="contract",
            payload={},
            response_schema=TextDiagnosisResult,
            model_profile=ModelProfile(model="test", thinking=False),
            user_instruction="Diagnose:",
        )

    assert caught.value.category is category


@pytest.mark.parametrize(
    ("error", "category"),
    [
        (
            anthropic.AuthenticationError(
                "bad key",
                response=httpx.Response(
                    401, request=httpx.Request("POST", "https://api.anthropic.com")
                ),
                body=None,
            ),
            ProviderErrorCategory.AUTHENTICATION,
        ),
        (
            anthropic.RateLimitError(
                "credit exhausted",
                response=httpx.Response(
                    429, request=httpx.Request("POST", "https://api.anthropic.com")
                ),
                body=None,
            ),
            ProviderErrorCategory.RATE_LIMIT,
        ),
        (
            anthropic.APITimeoutError(
                httpx.Request("POST", "https://api.anthropic.com")
            ),
            ProviderErrorCategory.NETWORK,
        ),
        (
            anthropic.BadRequestError(
                "invalid model configuration",
                response=httpx.Response(
                    400, request=httpx.Request("POST", "https://api.anthropic.com")
                ),
                body=None,
            ),
            ProviderErrorCategory.AUTHENTICATION,
        ),
        (
            anthropic.NotFoundError(
                "model not found",
                response=httpx.Response(
                    404, request=httpx.Request("POST", "https://api.anthropic.com")
                ),
                body=None,
            ),
            ProviderErrorCategory.AUTHENTICATION,
        ),
    ],
)
def test_anthropic_sdk_failures_map_to_provider_error_categories(error, category):
    provider = AnthropicProvider(_RaisingAnthropicClient(error))

    with pytest.raises(ProviderError) as caught:
        provider.generate(
            system_contract="contract",
            payload={},
            response_schema=TextDiagnosisResult,
            model_profile=ModelProfile(model="test", thinking=False),
            user_instruction="Diagnose:",
        )

    assert caught.value.category is category


def test_third_party_endpoint_never_receives_a_bare_anthropic_environment_key(
    monkeypatch,
):
    monkeypatch.setattr(config, "_file_values", {})
    monkeypatch.setenv("ANTHROPIC_BASE_URL", "https://api.deepseek.com/anthropic")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-must-not-leak")
    monkeypatch.delenv("POSTMORTEM_API_KEY", raising=False)

    with patch("postmortem.providers.anthropic_provider.anthropic.Anthropic") as ctor:
        with pytest.raises(ProviderError) as caught:
            AnthropicProvider.from_config()

    assert caught.value.category is ProviderErrorCategory.AUTHENTICATION
    ctor.assert_not_called()


def test_third_party_endpoint_uses_its_colocated_config_key(monkeypatch):
    monkeypatch.setattr(
        config,
        "_file_values",
        {
            "ANTHROPIC_BASE_URL": "https://api.deepseek.com/anthropic",
            "ANTHROPIC_API_KEY": "third-party-key",
        },
    )
    monkeypatch.delenv("ANTHROPIC_BASE_URL", raising=False)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-must-not-leak")
    monkeypatch.delenv("POSTMORTEM_API_KEY", raising=False)
    fake_client = object()

    with patch(
        "postmortem.providers.anthropic_provider.anthropic.Anthropic",
        return_value=fake_client,
    ) as ctor:
        provider, profile = AnthropicProvider.from_config()

    assert isinstance(provider, AnthropicProvider)
    assert profile.model == "claude-opus-4-8"
    ctor.assert_called_once_with(
        api_key="third-party-key",
        base_url="https://api.deepseek.com/anthropic",
    )


def test_injected_provider_path_does_not_import_an_sdk():
    script = r'''
import builtins

real_import = builtins.__import__
def blocked_import(name, *args, **kwargs):
    if name == "anthropic" or name.startswith("anthropic."):
        raise AssertionError("provider-independent path imported anthropic")
    return real_import(name, *args, **kwargs)
builtins.__import__ = blocked_import

from postmortem import diagnose
from postmortem.providers.base import ModelProfile

class TestProvider:
    def generate(self, **kwargs):
        return {"text": "provider injection works"}

print(diagnose.diagnose({}, provider=TestProvider(), profile=ModelProfile("test")))
'''
    result = subprocess.run(
        [sys.executable, "-c", script],
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "provider injection works"


@pytest.mark.parametrize(
    ("base_url", "key_name", "expected_kwargs"),
    [
        (
            None,
            "ANTHROPIC_API_KEY",
            {"api_key": "provider-key"},
        ),
        (
            "https://api.deepseek.com/anthropic",
            "POSTMORTEM_API_KEY",
            {
                "api_key": "provider-key",
                "base_url": "https://api.deepseek.com/anthropic",
            },
        ),
    ],
)
def test_anthropic_and_compatible_configurations_resolve_profiles(
    monkeypatch, base_url, key_name, expected_kwargs
):
    monkeypatch.setattr(config, "_file_values", {})
    for name in ("ANTHROPIC_API_KEY", "POSTMORTEM_API_KEY", "ANTHROPIC_BASE_URL"):
        monkeypatch.delenv(name, raising=False)
    if base_url:
        monkeypatch.setenv("ANTHROPIC_BASE_URL", base_url)
    monkeypatch.setenv(key_name, "provider-key")
    monkeypatch.setenv("POSTMORTEM_MODEL", "configured-model")
    monkeypatch.setenv("POSTMORTEM_THINKING", "off")

    with patch(
        "postmortem.providers.anthropic_provider.anthropic.Anthropic",
        return_value=object(),
    ) as ctor:
        _, profile = AnthropicProvider.from_config()

    ctor.assert_called_once_with(**expected_kwargs)
    assert profile == ModelProfile(model="configured-model", thinking=False)


def test_config_file_key_is_not_forwarded_to_an_environment_endpoint_override(
    monkeypatch,
):
    monkeypatch.setattr(
        config,
        "_file_values",
        {
            "ANTHROPIC_BASE_URL": "https://api.deepseek.com/anthropic",
            "ANTHROPIC_API_KEY": "deepseek-file-key",
        },
    )
    monkeypatch.setenv(
        "ANTHROPIC_BASE_URL", "https://different-provider.example/anthropic"
    )
    monkeypatch.delenv("POSTMORTEM_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)

    with patch("postmortem.providers.anthropic_provider.anthropic.Anthropic") as ctor:
        with pytest.raises(ProviderError):
            AnthropicProvider.from_config()

    ctor.assert_not_called()


def test_dedicated_config_file_key_works_with_an_environment_endpoint(monkeypatch):
    monkeypatch.setattr(
        config,
        "_file_values",
        {"POSTMORTEM_API_KEY": "dedicated-provider-key"},
    )
    monkeypatch.setenv("ANTHROPIC_BASE_URL", "https://provider.example/anthropic")
    monkeypatch.delenv("POSTMORTEM_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)

    with patch(
        "postmortem.providers.anthropic_provider.anthropic.Anthropic",
        return_value=object(),
    ) as ctor:
        AnthropicProvider.from_config()

    ctor.assert_called_once_with(
        api_key="dedicated-provider-key",
        base_url="https://provider.example/anthropic",
    )


def test_minimax_dev_key_is_only_used_for_the_verified_minimax_host(monkeypatch):
    monkeypatch.setattr(config, "_file_values", {})
    monkeypatch.setenv(
        "ANTHROPIC_BASE_URL", "https://minimax.attacker.example/anthropic"
    )
    monkeypatch.delenv("POSTMORTEM_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)

    with (
        patch(
            "postmortem.providers.anthropic_provider._first_line_matching",
            return_value="- Key: local-minimax-secret",
        ),
        patch(
            "postmortem.providers.anthropic_provider.anthropic.Anthropic"
        ) as ctor,
    ):
        with pytest.raises(ProviderError):
            AnthropicProvider.from_config()

    ctor.assert_not_called()


def test_verified_minimax_host_preserves_the_dev_machine_fallback(monkeypatch):
    monkeypatch.setattr(config, "_file_values", {})
    monkeypatch.setenv("ANTHROPIC_BASE_URL", "https://api.minimax.io/anthropic")
    for name in ("ANTHROPIC_API_KEY", "POSTMORTEM_API_KEY", "POSTMORTEM_MODEL"):
        monkeypatch.delenv(name, raising=False)

    with (
        patch(
            "postmortem.providers.anthropic_provider._first_line_matching",
            return_value="- Key: local-minimax-secret",
        ),
        patch(
            "postmortem.providers.anthropic_provider.anthropic.Anthropic",
            return_value=object(),
        ) as ctor,
    ):
        _, profile = AnthropicProvider.from_config()

    ctor.assert_called_once_with(
        api_key="local-minimax-secret",
        base_url="https://api.minimax.io/anthropic",
    )
    assert profile.model == "MiniMax-M3"


def test_client_construction_configuration_errors_are_typed(monkeypatch):
    monkeypatch.setattr(config, "_file_values", {})
    monkeypatch.delenv("ANTHROPIC_BASE_URL", raising=False)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "provider-key")

    with patch(
        "postmortem.providers.anthropic_provider.anthropic.Anthropic",
        side_effect=ValueError("invalid base URL"),
    ):
        with pytest.raises(ProviderError) as caught:
            AnthropicProvider.from_config()

    assert caught.value.category is ProviderErrorCategory.AUTHENTICATION
