import json
import os
from pathlib import Path
from typing import Dict, List, Optional


PROJECT_ROOT = Path(__file__).resolve().parent.parent
AGENT_DIR = PROJECT_ROOT / "agent"
DEFAULT_STORAGE_ROOT = (PROJECT_ROOT / "local-storage").resolve()
ENV_FILE = AGENT_DIR / ".env"
DIRECTORIES_FILE = AGENT_DIR / "directories.json"


def read_agent_settings(project_root: Path = PROJECT_ROOT) -> Dict[str, object]:
    agent_dir = project_root / "agent"
    default_data_dir = (agent_dir / "data").resolve()
    default_storage_root = (project_root / "local-storage").resolve()

    env_values: Dict[str, str] = {}
    env_file = agent_dir / ".env"
    if env_file.is_file():
        for line in env_file.read_text(encoding="utf-8").splitlines():
            stripped = line.strip()
            if not stripped or stripped.startswith("#") or "=" not in stripped:
                continue
            key, value = stripped.split("=", 1)
            value = value.strip()
            if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
                quote = value[0]
                value = value[1:-1]
                if quote == '"':
                    value = value.replace("\\\\", "\\").replace('\\"', '"')
            env_values[key.strip()] = value

    directories_file = agent_dir / "directories.json"
    directories: List[str] = []
    has_directories_file = directories_file.is_file()
    if has_directories_file:
        config = json.loads(directories_file.read_text(encoding="utf-8"))
        if not isinstance(config, dict) or not isinstance(config.get("directories"), list):
            raise ValueError(f"{directories_file} must contain a 'directories' list.")
        if not all(isinstance(item, str) for item in config["directories"]):
            raise ValueError(f"Every watched directory in {directories_file} must be a string.")

        for item in config["directories"]:
            p = Path(item).expanduser()
            if not p.is_absolute():
                p = (project_root / p).resolve()
            if p.is_dir():
                resolved_str = str(p.resolve())
                if resolved_str not in directories:
                    directories.append(resolved_str)
            elif item.replace("\\", "/").endswith("/agent/data"):
                default_data_dir.mkdir(parents=True, exist_ok=True)
                resolved_str = str(default_data_dir)
                if resolved_str not in directories:
                    directories.append(resolved_str)

    # If directories.json was present but all paths were from an old machine and didn't exist:
    if has_directories_file and not directories:
        env_dirs = env_values.get("WATCH_DIRS", "")
        for d in env_dirs.split(","):
            d = d.strip()
            if d:
                p = Path(d).expanduser()
                if not p.is_absolute():
                    p = (project_root / p).resolve()
                if p.is_dir():
                    resolved_str = str(p.resolve())
                    if resolved_str not in directories:
                        directories.append(resolved_str)
        if not directories:
            default_data_dir.mkdir(parents=True, exist_ok=True)
            directories.append(str(default_data_dir))

    # Resolve storage root safely
    raw_storage = env_values.get("CHICKENSEE_LOCAL_STORAGE", str(default_storage_root))
    storage_path = Path(raw_storage).expanduser()
    if not storage_path.is_absolute():
        storage_path = (project_root / storage_path).resolve()
    try:
        storage_path.mkdir(parents=True, exist_ok=True)
        resolved_storage = str(storage_path.resolve())
    except Exception:
        default_storage_root.mkdir(parents=True, exist_ok=True)
        resolved_storage = str(default_storage_root)

    return {
        "directories": directories,
        "storage_root": resolved_storage,
        "cloud_sync": env_values.get("ENABLE_CLOUD_SYNC", "0") == "1",
        "sync_url": env_values.get("VERCEL_SYNC_URL", ""),
        "sync_secret": env_values.get("SYNC_SECRET", ""),
        "env_values": env_values,
    }


def validate_agent_settings(settings: Dict[str, object]) -> Optional[str]:
    directories = settings.get("directories")
    if not isinstance(directories, list) or not directories:
        return "Add at least one existing video folder to watch."

    existing_dirs = [path for path in directories if isinstance(path, str) and Path(path).is_dir()]
    if not existing_dirs:
        return "Every watched folder must exist. Select an existing folder or create it first."

    storage_root = settings.get("storage_root")
    if not isinstance(storage_root, str) or not storage_root.strip():
        return "Choose a local storage folder."

    if settings.get("cloud_sync"):
        sync_url = settings.get("sync_url")
        if not isinstance(sync_url, str) or not sync_url.startswith("https://"):
            return "Cloud sync requires the HTTPS Vercel API URL that generates Supabase upload links."
        if not isinstance(settings.get("sync_secret"), str) or not settings["sync_secret"].strip():
            return "Cloud sync requires the sync secret configured in Vercel."
    return None


def write_agent_settings(
    settings: Dict[str, object], project_root: Path = PROJECT_ROOT
) -> None:
    error = validate_agent_settings(settings)
    if error:
        raise ValueError(error)

    agent_dir = project_root / "agent"
    agent_dir.mkdir(parents=True, exist_ok=True)

    storage_root = Path(str(settings["storage_root"])).expanduser()
    if not storage_root.is_absolute():
        storage_root = (project_root / storage_root).resolve()
    try:
        storage_root.mkdir(parents=True, exist_ok=True)
    except Exception:
        storage_root = (project_root / "local-storage").resolve()
        storage_root.mkdir(parents=True, exist_ok=True)

    # Filter to valid directories for this machine
    resolved_directories = []
    for path in settings["directories"]:
        p = Path(path).expanduser()
        if not p.is_absolute():
            p = (project_root / p).resolve()
        if p.is_dir():
            resolved = str(p.resolve())
            if resolved not in resolved_directories:
                resolved_directories.append(resolved)

    if not resolved_directories:
        default_dir = (agent_dir / "data").resolve()
        default_dir.mkdir(parents=True, exist_ok=True)
        resolved_directories = [str(default_dir)]

    (agent_dir / "directories.json").write_text(
        json.dumps({"directories": resolved_directories}, indent=2) + "\n",
        encoding="utf-8",
    )

    env_values = dict(settings.get("env_values", {}))
    env_values.update(
        {
            "VERCEL_SYNC_URL": str(settings.get("sync_url", "")).strip(),
            "SYNC_SECRET": str(settings.get("sync_secret", "")),
            "WATCH_DIRS": ",".join(Path(path).as_posix() for path in resolved_directories),
            "WATCH_DIR": Path(resolved_directories[0]).as_posix() if resolved_directories else "./data",
            "ENABLE_CLOUD_SYNC": "1" if settings.get("cloud_sync") else "0",
            "SYNC_VIDEOS_TO_CLOUD": "0",
            "CHICKENSEE_LOCAL_STORAGE": storage_root.as_posix(),
        }
    )
    env_file = agent_dir / ".env"
    with env_file.open("w", encoding="utf-8", newline="\n") as output:
        for key, value in env_values.items():
            safe_value = str(value).replace('"', '\\"')
            output.write(f'{key}="{safe_value}"\n')

    if os.name != "nt":
        os.chmod(env_file, 0o600)


def write_watch_directories(
    directories: List[str], project_root: Path = PROJECT_ROOT
) -> None:
    settings = read_agent_settings(project_root)
    settings["directories"] = directories
    write_agent_settings(settings, project_root)
