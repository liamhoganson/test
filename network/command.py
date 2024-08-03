import inspect
import importlib.util
from abc import ABC, abstractmethod
from pathlib import Path


class Command(ABC):
    @abstractmethod
    def execute(self, **kwargs):
        pass


class CommandPlatformResolver:
    def __init__(self):
        self._map = {}
        for file in (Path(__file__).parent / 'platforms').rglob('*.py'):
            if file.name.startswith('_'):
                continue

            spec = importlib.util.spec_from_file_location(file.stem, file)
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)

            for _, cls in inspect.getmembers(module, inspect.isclass):
                if issubclass(cls, Command) and cls is not Command:
                    self.set(file.stem, cls)

    def get(self, name):
        return self._map.get(name)

    def set(self, name, cls):
        self._map[name] = cls



class CommandExecutor:
    def __init__(self, conn):
        self.conn = conn
        self.resolver = CommandPlatformResolver()

    async def run(name, *args, **kwargs):
        command = self.resolver.get(name)
        return await command(*args, **kwargs)
