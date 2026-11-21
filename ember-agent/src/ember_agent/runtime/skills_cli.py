"""Skill commands operate on the running operator owner; no second Core or SDK in Ember."""

import base64
import json
import stat
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import httpx

from .config import DeploymentConfig
from .skills_contracts import SkillBundle


def package_from_path(path: Path) -> SkillBundle:
    """Read an explicitly selected portable package without following filesystem links."""
    if path.is_symlink():
        raise ValueError("skill_resource_link")
    if path.is_file():
        return SkillBundle.model_validate_json(path.read_bytes())
    files: dict[str, str] = {}
    total = 0
    for member in sorted(path.rglob("*")):
        if any(part.startswith(".") for part in member.relative_to(path).parts):
            raise ValueError("skill_resource_path")
        if member.is_symlink():
            raise ValueError("skill_resource_link")
        if stat.S_ISREG(member.stat().st_mode):
            size = member.stat().st_size
            total += size
            if total > 131072 or len(files) >= 128:
                raise ValueError("skill_package_too_large")
            files[member.relative_to(path).as_posix()] = base64.b64encode(
                member.read_bytes()
            ).decode()
    return SkillBundle(name=path.name, files=files)


def skill_command(
    config: DeploymentConfig, action: str, target: str | None, output: Path | None
) -> int:
    """Use the existing authenticated loopback control API; never print its activation secret."""
    try:
        activation = json.loads((config.runtime_root / "browser-activation.json").read_text())
        address: object = activation.get("url")
        if not isinstance(address, str):
            raise ValueError("activation_invalid")
        token = parse_qs(urlsplit(address).fragment)["activate"][0]
        origin = config.browser.endpoint
        with httpx.Client(base_url=origin, trust_env=False, timeout=35) as client:
            response = client.post(
                "/api/bootstrap", json={"token": token}, headers={"Origin": origin}
            )
            response.raise_for_status()
            if action == "list":
                response = client.get("/api/skills")
            elif action == "inspect" and target:
                response = client.get("/api/skills/document", params={"identity": target})
            elif action == "import" and target:
                package = package_from_path(Path(target))
                response = client.post(
                    "/api/skills/import",
                    json=package.model_dump(mode="json"),
                    headers={"Origin": origin},
                )
            elif action == "export" and target and output:
                response = client.get("/api/skills/export", params={"identity": target})
                response.raise_for_status()
                package = SkillBundle.model_validate_json(response.content)
                with output.open("x", encoding="utf-8") as destination:
                    destination.write(package.model_dump_json(indent=2) + "\n")
                print("Skill package exported.")
                return 0
            else:
                raise ValueError("skill_command_arguments")
            response.raise_for_status()
            print(json.dumps(response.json(), indent=2, ensure_ascii=False))
            return 0
    except (OSError, ValueError, httpx.HTTPError, KeyError):
        print("Skill command unavailable: check the running Ember instance, package or arguments.")
        return 2
