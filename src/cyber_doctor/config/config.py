import threading
from functools import lru_cache
from pathlib import Path

import yaml
import os

from cyber_doctor.env import get_app_root


class Config(object):
    __instance = None
    __lock = threading.Lock()

    def __init__(self):
        self._config = None

    @classmethod
    def get_instance(cls):
        with cls.__lock:
            if cls.__instance is None:
                cls.__instance = cls._load_config()
            return cls.__instance

    @classmethod
    def _load_config(cls):
        instance = Config()
        env = os.environ.get("PY_ENVIRONMENT")
        config_path = Path(__file__).resolve().parent / f"config-{env}.yaml"
        with config_path.open("r", encoding="utf-8") as f:
            setattr(instance, "_config", yaml.load(f, Loader=yaml.FullLoader))

        return instance

    @lru_cache(maxsize=128)
    def get_with_nested_params(self, *params):
        assert self._config is not None, "please load config first"
        conf = self._config
        for param in params:
            if param in conf:
                conf = conf[param]
            else:
                raise KeyError(f"{param} not found in config")

        return conf


if __name__ == "__main__":
    print(get_app_root())
