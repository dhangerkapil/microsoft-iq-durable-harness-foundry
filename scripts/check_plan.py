"""Plan gate: refuse a Terraform plan that would touch shared resources beyond the
approved additions.

Usage: python scripts/check_plan.py <plan.json>   (terraform show -json <plan> > plan.json)

Shared scopes are the shared resources and their resource groups: EXISTING_FOUNDRY_ACCOUNT_ID, EXISTING_SEARCH_SERVICE_ID
and EXISTING_FABRIC_CAPACITY_ID from .env.local, and the plan's own existing_* variables, so the gate knows them even
when one of the two is missing. Inside a shared scope a plan may only *create* items whose names match
scripts/approved-additions.json, or role assignments (listed for review). Updates and deletes there are allowed only
for items this state created (Terraform can't update what it doesn't own, except through actions, which are refused).
"""

from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path

REFUSED_TYPES = ("azapi_resource_action", "azapi_update_resource")
SHARED_SETTINGS = ("EXISTING_FOUNDRY_ACCOUNT_ID", "EXISTING_SEARCH_SERVICE_ID", "EXISTING_FABRIC_CAPACITY_ID")


def shared_scopes(plan: dict | None = None) -> list[str]:
    variables = (plan or {}).get("variables") or {}
    ids = [os.environ.get(k, "") for k in SHARED_SETTINGS]
    for k in SHARED_SETTINGS:
        variable = variables.get(k.lower())
        ids.append(str(variable.get("value") or "") if isinstance(variable, dict) else "")
    scopes = set()
    for rid in filter(None, ids):
        rid = rid.lower().rstrip("/")
        scopes.add(rid)
        scopes.add(rid.split("/providers/")[0])
    return sorted(scopes, key=len, reverse=True)


def approved_names() -> list[re.Pattern[str]]:
    path = Path(__file__).with_name("approved-additions.json")
    data = json.loads(path.read_text(encoding="utf-8"))
    return [re.compile(p) for p in data["namePatterns"]]


def target_of(values: dict) -> str:
    for key in ("parent_id", "scope", "resource_id", "id"):
        value = values.get(key)
        if isinstance(value, str) and value.startswith("/subscriptions/"):
            return value.lower()
    rg = values.get("resource_group_name")
    return f"resourcegroup:{rg.lower()}" if isinstance(rg, str) else ""


def main(plan_file: str) -> int:
    plan = json.loads(Path(plan_file).read_text(encoding="utf-8"))
    scopes = shared_scopes(plan)
    if not scopes:
        print("Plan gate: no shared resources configured; nothing to check.")
        return 0
    shared_groups = {s.split("/resourcegroups/")[1].split("/")[0] for s in scopes if "/resourcegroups/" in s}
    names = approved_names()
    violations, reviews, additions = [], [], []
    for rc in plan.get("resource_changes", []):
        if rc.get("mode") != "managed":
            continue
        actions = rc["change"]["actions"]
        if actions == ["no-op"] or actions == ["read"]:
            continue
        values = rc["change"].get("after") or rc["change"].get("before") or {}
        target = target_of(values)
        # Inside an item this solution owns (for example its own project in the shared account): not shared.
        if any(p.search(segment) for segment in target.split("/") for p in names):
            continue
        in_shared = any(target.startswith(s) for s in scopes) or (
            target.startswith("resourcegroup:") and target.split(":", 1)[1] in shared_groups)
        if not in_shared:
            continue
        address = rc["address"]
        if rc["type"] in REFUSED_TYPES:
            violations.append(f"{address}: {rc['type']} on a shared resource ({target})")
            continue
        if "delete" in actions and "create" in actions:
            violations.append(f"{address}: replace inside a shared resource ({target})")
            continue
        if "create" in actions:
            name = values.get("name") or ""
            if rc["type"] == "azurerm_role_assignment":
                reviews.append(f"{address}: role '{values.get('role_definition_name')}' at {target}")
            elif any(p.search(name) for p in names):
                additions.append(f"{address}: {name} in {target}")
            else:
                violations.append(f"{address}: '{name}' in {target} is not an approved name")
        else:
            # Update or delete of an item this state created (it can only be ours).
            reviews.append(f"{address}: {'/'.join(actions)} of this solution's own item in {target}")
    print(f"Plan gate: {len(additions)} approved additions, {len(reviews)} for review, {len(violations)} violations.")
    for line in additions:
        print(f"  + {line}")
    for line in reviews:
        print(f"  ? {line}")
    for line in violations:
        print(f"  ! {line}")
    return 1 if violations else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1]))
