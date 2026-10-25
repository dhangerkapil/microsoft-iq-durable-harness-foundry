"""The agent's optimizer-ready configuration: instructions, skills and model.

The baseline lives in ``.agent_configs/baseline`` (``metadata.yaml``, ``instructions.md``, ``skills/<name>/SKILL.md``),
the layout the Foundry agent optimizer reads and writes (``azure-ai-agentserver-optimization``). During an
optimization run the platform supplies a candidate instead, inline (``OPTIMIZATION_CONFIG``) or through its resolver
(``OPTIMIZATION_CANDIDATE_ID`` with ``OPTIMIZATION_RESOLVE_ENDPOINT``); ``load_config()`` returns whichever applies.
Candidate skills that arrive inline are written to a private folder, because the harness loads skills from folders.
"""

from __future__ import annotations

import hashlib
import logging
import os
import re
import tempfile
import threading
from dataclasses import dataclass
from pathlib import Path

import yaml
from azure.ai.agentserver.optimization import OptimizationConfig, Skill, load_config

logger = logging.getLogger(__name__)

SKILL_NAME = re.compile(r"^[a-z0-9][a-z0-9-]{0,63}$")
_lock = threading.Lock()
_cache: dict[str, AgentSettings] = {}


@dataclass(frozen=True)
class AgentSettings:
    instructions: str
    skills_dir: Path
    model: str
    source: str


def config_dir() -> Path:
    """The configuration folder: ``OPTIMIZATION_LOCAL_DIR`` if set, else ``.agent_configs/<TEAM_MODE>`` (team or solo)
    in the project root next to ``src/``. Each mode has its own optimizer-ready baseline."""
    explicit = os.environ.get(OptimizationConfig.ENV_LOCAL_DIR, "").strip()
    if explicit:
        return Path(explicit).resolve()
    mode = os.environ.get("TEAM_MODE", "team").strip().lower() or "team"
    return Path(__file__).resolve().parents[2] / OptimizationConfig.DEFAULT_LOCAL_DIR / mode


def baseline_dir() -> Path:
    return config_dir() / OptimizationConfig.BASELINE_DIR


def _write_skills(skills: list[Skill]) -> Path:
    root = Path(tempfile.mkdtemp(prefix="advisor-skills-"))
    for skill in skills:
        if not SKILL_NAME.match(skill.name or ""):
            raise ValueError(f"Optimization candidate has an invalid skill name: {skill.name!r}")
        front = yaml.safe_dump({"name": skill.name, "description": skill.description or ""},
                               sort_keys=False, allow_unicode=True, width=1_000).strip()
        folder = root / skill.name
        folder.mkdir()
        (folder / OptimizationConfig.SKILL_FILE).write_text(f"---\n{front}\n---\n\n{skill.body}\n", encoding="utf-8")
    return root


def _cache_key() -> str:
    names = (OptimizationConfig.ENV_CONFIG, OptimizationConfig.ENV_CANDIDATE_ID,
             OptimizationConfig.ENV_RESOLVE_ENDPOINT, OptimizationConfig.ENV_LOCAL_DIR)
    return hashlib.sha256("\0".join(os.environ.get(name, "") for name in names).encode()).hexdigest()


def load_agent_settings(deployment: str | None = None, credential=None) -> AgentSettings:
    """Resolve instructions, skills and model once per process and configuration source.

    A local configuration (the baseline, or an applied candidate) runs on the deployment's model when the
    deployment names one; a candidate under evaluation runs on its own model, so model selection works.
    """
    key = f"{_cache_key()}|{deployment or ''}"
    with _lock:
        if key in _cache:
            return _cache[key]
        config = load_config(config_dir=config_dir(), credential=credential)
        if config is None or not config.instructions:
            raise RuntimeError(f"No agent configuration with instructions under {config_dir()}.")
        if config.skills:
            skills_dir = _write_skills(config.skills)
        elif config.skills_dir:
            skills_dir = Path(config.skills_dir)
        else:
            skills_dir = baseline_dir() / OptimizationConfig.SKILLS_DIR
        local = config.source.startswith("local:")
        model = (deployment or config.model) if local else (config.model or deployment)
        if not model:
            raise RuntimeError("No model deployment: set AZURE_AI_MODEL_DEPLOYMENT_NAME or model in metadata.yaml.")
        settings = AgentSettings(config.instructions, skills_dir, model, config.source)
        logger.info("Agent configuration source=%s model=%s instructions=%d chars skills=%s",
                    settings.source, settings.model, len(settings.instructions), settings.skills_dir)
        _cache[key] = settings
        return settings
