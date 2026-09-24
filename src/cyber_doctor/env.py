import os
from pathlib import Path

from dotenv import load_dotenv


PROJECT_ROOT = Path(__file__).resolve().parents[2]
load_dotenv(PROJECT_ROOT / ".env", override=False)


def get_app_root():
    configured_root = os.environ.get("CYBER_DOCTOR_ROOT")
    return str(Path(configured_root).expanduser().resolve() if configured_root else PROJECT_ROOT)


def get_env_value(key):
    return os.environ.get(key)


if __name__ == '__main__':
    print("app root is: " +get_app_root())
    print("model name is: " + str(get_env_value("MODEL_NAME") or "not configured"))
