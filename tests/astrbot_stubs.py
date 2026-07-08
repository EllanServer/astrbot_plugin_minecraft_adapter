from __future__ import annotations

import sys
import tempfile
import types
from pathlib import Path


class LoggerStub:
    def debug(self, *args, **kwargs):
        pass

    def info(self, *args, **kwargs):
        pass

    def warning(self, *args, **kwargs):
        pass

    def error(self, *args, **kwargs):
        pass


class CommandGroupDecoratorStub:
    def __call__(self, func):
        return self

    @staticmethod
    def command(*_args, **_kwargs):
        return lambda func: func


class FilterStub:
    @staticmethod
    def command_group(*_args, **_kwargs):
        return CommandGroupDecoratorStub()


class StarStub:
    def __init__(self, context=None):
        self.context = context


class MessageChainStub:
    def __init__(self, nodes=None):
        self.nodes = list(nodes or [])

    def __iter__(self):
        return iter(self.nodes)

    def __len__(self):
        return len(self.nodes)

    def __getitem__(self, index):
        return self.nodes[index]


class PlainStub:
    def __init__(self, text=""):
        self.text = str(text)


class FileStub:
    def __init__(self, file=""):
        self.file = str(file)


class ImageStub:
    @classmethod
    def fromBytes(cls, value):
        return types.SimpleNamespace(image=value)


def _module(name: str, *, package: bool = False):
    module = sys.modules.get(name) or types.ModuleType(name)
    if package and not hasattr(module, "__path__"):
        module.__path__ = []
    sys.modules[name] = module
    return module


def ensure_test_import_paths() -> None:
    repo_root = Path(__file__).resolve().parents[1]
    package_parent = repo_root.parent
    for path in (str(repo_root), str(package_parent)):
        if path not in sys.path:
            sys.path.insert(0, path)


def install_astrbot_stubs() -> None:
    """Install minimal AstrBot modules used by MineSentinel tests."""

    astrbot = _module("astrbot", package=True)
    api = _module("astrbot.api", package=True)
    api.logger = getattr(api, "logger", LoggerStub())
    astrbot.api = api

    event_module = _module("astrbot.api.event")
    event_module.AstrMessageEvent = getattr(event_module, "AstrMessageEvent", object)
    event_module.MessageChain = getattr(event_module, "MessageChain", MessageChainStub)
    event_module.filter = getattr(event_module, "filter", FilterStub())
    api.event = event_module

    star_module = _module("astrbot.api.star")
    star_module.Context = getattr(star_module, "Context", object)
    star_module.Star = getattr(star_module, "Star", StarStub)
    api.star = star_module

    core_module = _module("astrbot.core", package=True)
    core_star_module = _module("astrbot.core.star", package=True)
    core_filter_module = _module("astrbot.core.star.filter", package=True)
    command_module = _module("astrbot.core.star.filter.command")
    command_module.GreedyStr = getattr(command_module, "GreedyStr", str)
    astrbot.core = core_module
    core_module.star = core_star_module
    core_star_module.filter = core_filter_module
    core_filter_module.command = command_module

    message_components_module = _module("astrbot.api.message_components")
    message_components_module.Plain = getattr(
        message_components_module,
        "Plain",
        PlainStub,
    )
    message_components_module.File = getattr(
        message_components_module,
        "File",
        FileStub,
    )
    message_components_module.Image = getattr(
        message_components_module,
        "Image",
        ImageStub,
    )
    api.message_components = message_components_module

    utils_module = _module("astrbot.core.utils", package=True)
    path_module = _module("astrbot.core.utils.astrbot_path")
    path_module.get_astrbot_data_path = getattr(
        path_module,
        "get_astrbot_data_path",
        lambda: tempfile.gettempdir(),
    )
    core_module.utils = utils_module
    utils_module.astrbot_path = path_module
