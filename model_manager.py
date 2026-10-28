"""List and activate versioned poultry YOLO weights."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from model_registry import (
    activate_model,
    discover_models,
    install_model,
    registry_path,
    resolve_model_path,
    sha256_file,
)


PROJECT_ROOT = Path(__file__).resolve().parent


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description="Select which compatible .pt weights the edge and analytics services load."
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("list", help="List managed weights and the active model")
    install_parser = subparsers.add_parser(
        "install", help="Copy a .pt file into models/ as a new version and activate it"
    )
    install_parser.add_argument("path", help="Source .pt file")
    install_parser.add_argument("--name", help="Optional version filename or label")
    activate_parser = subparsers.add_parser("activate", help="Activate a .pt file")
    activate_parser.add_argument("path", nargs="?", help="Path to a .pt file")
    activate_parser.add_argument(
        "--latest",
        action="store_true",
        help="Activate the most recently modified .pt file in models/",
    )
    args = parser.parse_args(argv)

    try:
        if args.command == "list":
            env_override = os.getenv("MODEL_PATH")
            active_path = Path(
                resolve_model_path(PROJECT_ROOT, env_path=env_override)
            )
            active_manifest = registry_path(PROJECT_ROOT)
            manifest_data = (
                json.loads(active_manifest.read_text(encoding="utf-8"))
                if active_manifest.is_file() and not env_override
                else None
            )
            active_sha = manifest_data.get("sha256") if manifest_data else None
            print(f"Active model: {active_path}")
            if env_override:
                print("  MODEL_PATH environment override is active; registry changes will not take effect until it is cleared.")
            if active_sha and active_path.is_file():
                actual_sha = sha256_file(active_path)
                state = "matches" if actual_sha == active_sha else "CHANGED"
                print(f"  SHA-256 {state}: {actual_sha}")
                if actual_sha != active_sha:
                    print("  Re-activate this file to update the registry checksum.")
            candidates = discover_models(PROJECT_ROOT)
            legacy = PROJECT_ROOT / "custom_poultry.pt"
            if legacy.is_file() and legacy.resolve() not in [p.resolve() for p in candidates]:
                candidates.append(legacy)
            print("Available models:")
            if not candidates:
                print("  No .pt files found. Put versioned weights in models/.")
            for candidate in candidates:
                marker = " (active)" if candidate.resolve() == active_path.resolve() else ""
                print(f"  {candidate.relative_to(PROJECT_ROOT) if candidate.is_relative_to(PROJECT_ROOT) else candidate}{marker}")
            return 0

        if args.command == "install":
            data = install_model(args.path, PROJECT_ROOT, args.name)
            print(f"Installed and activated {data['version']}: {data['installed_path']}")
            print(f"SHA-256: {data['sha256']}")
            print(f"Manifest: {registry_path(PROJECT_ROOT)}")
            print("Restart the running service to load the new weights.")
            if os.getenv("MODEL_PATH"):
                print("Warning: MODEL_PATH overrides the registry. Clear it to use this selection.")
            return 0

        if args.latest == bool(args.path):
            parser.error("provide exactly one model path or --latest")
        if args.latest:
            candidates = discover_models(PROJECT_ROOT)
            if not candidates:
                raise FileNotFoundError("No .pt files found in models/.")
            selected = candidates[0]
        else:
            selected = Path(args.path).expanduser()
            if not selected.is_absolute():
                selected = PROJECT_ROOT / selected

        data = activate_model(selected, PROJECT_ROOT)
        print(f"Activated {data['version']}: {selected.resolve()}")
        print(f"Manifest: {registry_path(PROJECT_ROOT)}")
        if os.getenv("MODEL_PATH"):
            print("Warning: MODEL_PATH overrides the registry. Clear it to use this selection.")
        print("Restart the running service to load the new weights.")
        return 0
    except (FileNotFoundError, OSError, ValueError) as error:
        print(f"Model update failed: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
