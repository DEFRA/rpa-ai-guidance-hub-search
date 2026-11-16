"""Tests for the Bedrock LLM module."""

from app import config as app_config
from app.infra.bedrock import llm


class TestClaudeSonnet:
    def test_created_without_guardrails(self) -> None:
        model = llm.claude_sonnet(app_config.get_config())

        assert "bedrock_guardrail_config" not in model.settings

    def test_created_with_guardrails(self, mocker) -> None:
        mock_config = mocker.Mock()
        mock_config.aws_region = "eu-west-2"
        mock_config.bedrock.claude_sonnet.model_id = "anthropic.claude-sonnet-4-6"
        mock_config.bedrock.claude_sonnet.inference_profile = (
            "arn:aws:bedrock:eu-west-2:123456789012:application-inference-profile/test"
        )
        mock_config.bedrock.claude_sonnet.guardrails.id = "guardrail-123"
        mock_config.bedrock.claude_sonnet.guardrails.version = "1"

        model = llm.claude_sonnet(mock_config)

        assert model.settings["bedrock_guardrail_config"] == {
            "guardrailIdentifier": "guardrail-123",
            "guardrailVersion": "1",
            "trace": "enabled",
        }
