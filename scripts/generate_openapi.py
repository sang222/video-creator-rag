import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

try:
    import fastapi  # noqa: F401
except ModuleNotFoundError:
    version = f"{sys.version_info.major}.{sys.version_info.minor}"
    candidates = [
        Path(sys.prefix) / "lib" / f"python{version}" / "site-packages",
        Path(sys.base_prefix) / "lib" / f"python{version}" / "site-packages",
    ]
    framework_root = Path("/Library/Frameworks/Python.framework/Versions")
    if framework_root.exists():
        candidates.extend(framework_root.glob(f"*/lib/python{version}/site-packages"))
    for candidate in candidates:
        if (candidate / "fastapi").exists() and str(candidate) not in sys.path:
            sys.path.append(str(candidate))
            break

from app.main import app  # noqa: E402


def main() -> None:
    schema = app.openapi()
    output_path = Path("openapi.json")
    output_path.write_text(json.dumps(schema, indent=2, sort_keys=True, default=str) + "\n", encoding="utf-8")
    print(f"generated {output_path} with {len(schema.get('paths', {}))} paths")


if __name__ == "__main__":
    main()
