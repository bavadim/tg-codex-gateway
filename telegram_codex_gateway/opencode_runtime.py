import json
import os
import shutil
import tempfile
from dataclasses import dataclass
from importlib import resources
from pathlib import Path
from typing import Dict, List, Optional


PACKAGE_RESOURCES = "telegram_codex_gateway.resources.opencode"
PERSISTENT_RUNTIME_ROOT = Path("/tmp/tg-agent-gateway/opencode")


@dataclass
class OpenCodeRuntime:
    env: Dict[str, str]
    temp_dir: Path
    data_dir: Path
    cache_dir: Path


def _copy_resource_tree(source, destination: Path) -> None:
    destination.mkdir(parents=True, exist_ok=True)
    for item in source.iterdir():
        target = destination / item.name
        if item.is_dir():
            _copy_resource_tree(item, target)
            continue
        target.write_bytes(item.read_bytes())


def _build_runtime_instructions() -> List[str]:
    instructions = [
        "You are running inside a Telegram gateway.",
        "Preserve project-local AGENTS.md and project-local skills from the working directory.",
    ]
    return instructions


def _build_permission_config() -> Dict[str, str]:
    return {
        "read": "allow",
        "edit": "allow",
        "glob": "allow",
        "grep": "allow",
        "list": "allow",
        "bash": "allow",
        "task": "allow",
        "todowrite": "allow",
        "todoread": "allow",
        "webfetch": "allow",
        "websearch": "allow",
        "codesearch": "allow",
        "lsp": "allow",
        "skill": "allow",
        "doom_loop": "ask",
        "question": "allow",
        "external_directory": "deny",
    }


def _build_agent_config() -> Dict[str, object]:
    return {
        "default_agent": "build",
        "agent": {
            "plan": {
                "disable": True,
            }
        },
    }


def _split_provider_model(raw_model: str) -> tuple[str, str]:
    provider, _, model_name = raw_model.partition("/")
    if provider and model_name:
        return provider, model_name
    return "myopenai", raw_model


def _build_provider_config(env: Dict[str, str]) -> Optional[Dict[str, object]]:
    api_key = env.get("OPENAI_API_KEY")
    api_base = env.get("OPENAI_API_BASE")
    raw_model = env.get("OPENCODE_MODEL", "myopenai/gpt-5")
    provider_id, model_name = _split_provider_model(raw_model)

    if not api_key or not api_base:
        return None

    return {
        "model": f"{provider_id}/{model_name}",
        "provider": {
            provider_id: {
                "npm": "@ai-sdk/openai",
                "name": "OpenAI Responses",
                "options": {
                    "apiKey": "{env:OPENAI_API_KEY}",
                    "baseURL": "{env:OPENAI_API_BASE}",
                },
                "models": {
                    model_name: {
                        "name": model_name,
                    }
                },
            }
        },
    }


def _build_persistent_runtime_dirs(chat_id: int) -> tuple[Path, Path]:
    root = PERSISTENT_RUNTIME_ROOT / str(chat_id)
    data_dir = root / "xdg-data"
    cache_dir = root / "xdg-cache"
    data_dir.mkdir(parents=True, exist_ok=True)
    cache_dir.mkdir(parents=True, exist_ok=True)
    return data_dir, cache_dir


def build_opencode_runtime(base_env: Dict[str, str], chat_id: int) -> OpenCodeRuntime:
    temp_dir = Path(tempfile.mkdtemp(prefix="tg-opencode-"))
    config_dir = temp_dir / "config"
    config_dir.mkdir(parents=True, exist_ok=True)
    data_dir, cache_dir = _build_persistent_runtime_dirs(chat_id)

    skills_root = config_dir / "skills"
    skills_source = resources.files(PACKAGE_RESOURCES).joinpath("skills")
    if skills_source.is_dir():
        _copy_resource_tree(skills_source, skills_root)

    env = dict(base_env)
    env["OPENCODE_CONFIG_DIR"] = str(config_dir)
    env.setdefault("XDG_DATA_HOME", str(data_dir))
    env.setdefault("XDG_CACHE_HOME", str(cache_dir))

    instructions = _build_runtime_instructions()
    content: Dict[str, object] = {"instructions": instructions} if instructions else {}
    content["permission"] = _build_permission_config()
    content.update(_build_agent_config())
    provider_config = _build_provider_config(env)
    if provider_config:
        content.update(provider_config)

    existing = env.get("OPENCODE_CONFIG_CONTENT")
    if existing:
        try:
            existing_payload = json.loads(existing)
        except json.JSONDecodeError:
            existing_payload = {}
        if isinstance(existing_payload, dict):
            if instructions:
                merged = list(existing_payload.get("instructions", []))
                merged.extend(instructions)
                existing_payload["instructions"] = merged
            existing_payload["permission"] = _build_permission_config()
            existing_payload["default_agent"] = "build"
            agents = existing_payload.setdefault("agent", {})
            if isinstance(agents, dict):
                plan = agents.setdefault("plan", {})
                if isinstance(plan, dict):
                    plan["disable"] = True
            if provider_config:
                existing_payload.setdefault("model", provider_config["model"])
                providers = existing_payload.setdefault("provider", {})
                if isinstance(providers, dict):
                    providers.update(provider_config["provider"])
            content = existing_payload
    if content:
        env["OPENCODE_CONFIG_CONTENT"] = json.dumps(content)

    return OpenCodeRuntime(
        env=env,
        temp_dir=temp_dir,
        data_dir=data_dir,
        cache_dir=cache_dir,
    )


def cleanup_opencode_runtime(runtime: OpenCodeRuntime) -> None:
    shutil.rmtree(runtime.temp_dir, ignore_errors=True)
