# Provider adapter extension guide

Post Mortem keeps model SDKs behind `DiagnosisProvider`, defined in
`postmortem/providers/base.py`. Diagnosis orchestration passes ordinary Python
mappings in and receives a JSON-compatible mapping out; provider response
objects must not escape the adapter.

## Implement the contract

Create a module in `postmortem/providers/` with a class that implements:

```python
def generate(
    self,
    *,
    system_contract: str,
    payload: Mapping[str, Any],
    response_schema: type[BaseModel],
    model_profile: ModelProfile,
    user_instruction: str,
) -> dict[str, Any]:
    ...
```

The adapter must:

1. Build the provider request from the supplied contract, instruction, payload,
   response schema, and model profile.
2. Require one complete structured response. If the provider has no native
   schema enforcement, extract exactly one top-level JSON object without
   evaluating text.
3. Validate with `response_schema.model_validate(...)` inside the adapter and
   return `validated.model_dump(mode="json")`.
4. Translate SDK failures to `ProviderError`; never expose SDK exceptions or
   response objects to the CLI.
5. Keep endpoint, credential, and model selection in the adapter factory.

`TextDiagnosisResult` exists only for the legacy cross-track prose path. New
single-track integrations should expect `DiagnosisResult`.

## Stable errors

Map failures to the narrowest `ProviderErrorCategory`:

- `AUTHENTICATION`: missing or rejected credentials, endpoint, model, or request
  configuration.
- `RATE_LIMIT`: rate limiting, quota, or exhausted credit.
- `NETWORK`: connection failures, timeouts, and unclassified service failures.
- `REFUSAL`: an explicit provider refusal.
- `INCOMPLETE_RESPONSE`: empty or truncated output.
- `INVALID_RESPONSE`: JSON extraction or schema validation failed.

The CLI owns the user-facing line and exit code. Refusal and invalid structured
output become a validated non-actionable Track Check result; other operational
errors remain typed CLI failures.

## Credential boundary

Resolve the base URL and its credential as one profile. Never forward a key
chosen for one provider to a different host. The built-in Anthropic-compatible
adapter requires `POSTMORTEM_API_KEY` for a third-party endpoint (or a key stored
with that endpoint in the config file); a bare Anthropic environment key is not
reused across hosts. Apply the same-source rule to every new adapter.

Do not log keys, authorization headers, raw provider responses that may contain
them, or private paths from local config.

## Inject and test

`diagnose_track(payload, provider=adapter, profile=ModelProfile(...))` accepts an
adapter directly. Unit tests should use a small fake and must not import the real
SDK on the provider-independent path:

```python
class FixtureProvider:
    def __init__(self, result):
        self.result = result

    def generate(self, **request):
        return request["response_schema"].model_validate(
            self.result
        ).model_dump(mode="json")
```

Cover valid output, refusal, empty/truncated output, malformed JSON, schema
failure, and each error mapping. If the provider repairs malformed structured
output, allow at most one compact repair attempt and validate the repaired
object through the same schema. Run the commands in `docs/DEVELOPING.md` before
opening a pull request.
