"""The plan gate knows the shared resources from .env.local and from the plan's own variables."""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import check_plan  # noqa: E402

ACCOUNT = ("/subscriptions/00000000-0000-0000-0000-000000000000/resourceGroups/rg-shared/providers/"
           "Microsoft.CognitiveServices/accounts/shared-ai")
NOTHING_SHARED = {"existing_foundry_account_id": "", "existing_search_service_id": "", "existing_fabric_capacity_id": ""}
SHARED_ACCOUNT = {**NOTHING_SHARED, "existing_foundry_account_id": ACCOUNT}


def plan_with(variables: dict, name: str) -> dict:
    """A plan that creates one item, `name`, inside the account."""
    return {
        "variables": {key: {"value": value} for key, value in variables.items()},
        "resource_changes": [{
            "mode": "managed", "address": "azapi_resource.item", "type": "azapi_resource",
            "change": {"actions": ["create"], "after": {"name": name, "parent_id": ACCOUNT}},
        }],
    }


def gate(tmp_path, plan: dict) -> int:
    path = tmp_path / "plan.json"
    path.write_text(json.dumps(plan), encoding="utf-8")
    return check_plan.main(str(path))


def clear_settings(monkeypatch) -> None:
    for key in check_plan.SHARED_SETTINGS:
        monkeypatch.delenv(key, raising=False)


def test_the_plan_names_the_shared_resources_when_env_local_does_not(tmp_path, monkeypatch, capsys):
    clear_settings(monkeypatch)
    assert check_plan.shared_scopes(plan_with(SHARED_ACCOUNT, "x")) == [
        ACCOUNT.lower(), ACCOUNT.lower().split("/providers/")[0]]
    assert gate(tmp_path, plan_with(SHARED_ACCOUNT, "someone-elses-deployment")) == 1
    assert "is not an approved name" in capsys.readouterr().out
    assert gate(tmp_path, plan_with(SHARED_ACCOUNT, "team-lead")) == 0


def test_env_local_still_names_them_and_a_deployment_without_shared_resources_passes(tmp_path, monkeypatch, capsys):
    clear_settings(monkeypatch)
    assert gate(tmp_path, plan_with(NOTHING_SHARED, "someone-elses-deployment")) == 0
    assert "no shared resources configured" in capsys.readouterr().out
    monkeypatch.setenv("EXISTING_FOUNDRY_ACCOUNT_ID", ACCOUNT)
    assert gate(tmp_path, plan_with(NOTHING_SHARED, "someone-elses-deployment")) == 1
