"""The specialists' model client waits out a rate limit rather than reporting its IQ unavailable."""

from contextlib import AsyncExitStack

from advisor_team import team


class Credential:
    async def get_token(self, *scopes, **kwargs):
        raise AssertionError("building the client makes no call")


def test_the_specialists_own_deployment_waits_out_rate_limits(monkeypatch):
    monkeypatch.setenv("TEAM_SPECIALIST_ENDPOINT", "https://aisp-example.openai.azure.com/openai/v1/")
    monkeypatch.setenv("TEAM_SPECIALIST_DEPLOYMENT", "team-specialist")
    client = team._specialist_client("https://project.example", Credential(), {}, AsyncExitStack(), object)
    assert client.client.max_retries == team.SPECIALIST_MAX_RETRIES == 5
