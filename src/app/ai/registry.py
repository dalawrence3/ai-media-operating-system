"""Prompt registry — loads versioned TOML prompt files from the repository.

Prompts live under ``src/app/ai/prompts/{name}/v{version}.toml``.
The registry validates metadata on load and caches parsed prompts in memory.
"""

from __future__ import annotations

import tomllib
from dataclasses import dataclass
from pathlib import Path

from app.ai.errors import PromptMetadataError, PromptNotFoundError

_REQUIRED = {"name", "version", "description", "system", "user_template"}


@dataclass(frozen=True)
class Prompt:
    name: str
    version: str
    description: str
    system: str
    user_template: str

    def format_user(self, **kwargs: str) -> str:
        """Substitute template variables.  Raises ValueError on missing keys."""
        try:
            return self.user_template.format(**kwargs)
        except KeyError as exc:
            raise ValueError(
                f"Missing template variable {exc} in prompt {self.name!r} v{self.version}"
            ) from exc

    @classmethod
    def load(cls, path: Path) -> Prompt:
        try:
            with open(path, "rb") as fh:
                data = tomllib.load(fh)
        except tomllib.TOMLDecodeError as exc:
            raise PromptMetadataError(f"Malformed TOML in {path}: {exc}") from exc

        missing = _REQUIRED - data.keys()
        if missing:
            raise PromptMetadataError(
                f"Prompt file {path} is missing required fields: {sorted(missing)}"
            )
        for field in _REQUIRED:
            if not isinstance(data[field], str) or not data[field].strip():
                raise PromptMetadataError(
                    f"Prompt file {path}: field {field!r} must be a non-empty string"
                )

        return cls(
            name=data["name"].strip(),
            version=str(data["version"]).strip(),
            description=data["description"].strip(),
            system=data["system"],
            user_template=data["user_template"],
        )


class PromptRegistry:
    """Loads and caches prompts by (name, version) pair."""

    def __init__(self, prompts_dir: Path | None = None) -> None:
        self._dir = prompts_dir or Path(__file__).parent / "prompts"
        self._cache: dict[tuple[str, str], Prompt] = {}

    def get(self, name: str, version: str) -> Prompt:
        """Return the named prompt.  Raises on missing or malformed files."""
        key = (name, version)
        if key in self._cache:
            return self._cache[key]

        path = self._dir / name / f"v{version}.toml"
        if not path.exists():
            raise PromptNotFoundError(name, version)

        prompt = Prompt.load(path)
        if prompt.name != name:
            raise PromptMetadataError(
                f"Prompt name mismatch: file declares {prompt.name!r}, path implies {name!r}"
            )
        if prompt.version != version:
            raise PromptMetadataError(
                f"Prompt version mismatch: file declares {prompt.version!r},"
                f" path implies {version!r}"
            )

        self._cache[key] = prompt
        return prompt

    def list_all(self) -> list[Prompt]:
        """Return all discoverable prompts, sorted by name then version."""
        results: list[Prompt] = []
        if not self._dir.exists():
            return results
        for name_dir in sorted(self._dir.iterdir()):
            if not name_dir.is_dir():
                continue
            for version_file in sorted(name_dir.glob("v*.toml")):
                version = version_file.stem[1:]  # strip leading 'v'
                try:
                    results.append(self.get(name_dir.name, version))
                except (PromptNotFoundError, PromptMetadataError):
                    pass
        return results
