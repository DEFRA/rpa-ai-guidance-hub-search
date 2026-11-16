from pydantic_ai.models import bedrock as bedrock_models
from pydantic_ai.providers import bedrock as bedrock_providers

from app import config


def claude_sonnet(app_config: config.AppConfig) -> bedrock_models.BedrockConverseModel:
    """Claude Sonnet on Bedrock, with its guardrails if configured."""
    provider = bedrock_providers.BedrockProvider(region_name=app_config.aws_region)

    return _setup_model(
        app_config.bedrock.claude_sonnet,  # type: ignore[attr-defined]
        provider,
    )


def _setup_model(
    model_config: config.BedrockModelConfig,
    provider: bedrock_providers.BedrockProvider,
) -> bedrock_models.BedrockConverseModel:
    """Create a BedrockConverseModel from configuration."""

    def build_settings() -> bedrock_models.BedrockModelSettings:
        settings = bedrock_models.BedrockModelSettings(
            bedrock_inference_profile=model_config.inference_profile,
            temperature=0.0,
        )

        if model_config.guardrails:
            settings["bedrock_guardrail_config"] = {
                "guardrailIdentifier": model_config.guardrails.id,
                "guardrailVersion": model_config.guardrails.version,
                "trace": "enabled",
            }

        return settings

    model_name = model_config.model_id

    return bedrock_models.BedrockConverseModel(
        model_name,
        provider=provider,
        settings=build_settings(),
    )
