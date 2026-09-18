"""Provider registry (spec section 9.1): dict[SourceKind, factory].

All three V1 engines are implemented as of M2. Each provider passes the shared
contract tests and owns its engine-specific semantics (timeouts, cancel,
types); there is no if/else branching between engines in business modules.
"""

from __future__ import annotations

from collections.abc import Callable

from app.config import Settings
from app.constants import SourceKind
from app.models.orm import Datasource
from app.providers.base import ProviderCredentials, QueryProvider

ProviderFactory = Callable[[Datasource, ProviderCredentials, Settings], QueryProvider]


class ProviderRegistry:
    def __init__(self) -> None:
        self._factories: dict[str, ProviderFactory] = {}

    def register(self, kind: str, factory: ProviderFactory) -> None:
        self._factories[str(kind)] = factory

    def kinds(self) -> list[str]:
        return sorted(self._factories)

    def create(
        self, kind: str, datasource: Datasource, credentials: ProviderCredentials, settings: Settings
    ) -> QueryProvider:
        factory = self._factories.get(str(kind))
        if factory is None:
            raise KeyError(f"no provider registered for kind '{kind}'")
        return factory(datasource, credentials, settings)


def build_default_registry() -> ProviderRegistry:
    from app.providers.doris import DorisProvider
    from app.providers.mysql import MySQLProvider
    from app.providers.postgres import PostgresProvider

    registry = ProviderRegistry()
    registry.register(SourceKind.POSTGRES, PostgresProvider.from_datasource)
    registry.register(SourceKind.MYSQL, MySQLProvider.from_datasource)
    registry.register(SourceKind.DORIS, DorisProvider.from_datasource)
    return registry


_REGISTRY: ProviderRegistry | None = None


def get_registry() -> ProviderRegistry:
    global _REGISTRY
    if _REGISTRY is None:
        _REGISTRY = build_default_registry()
    return _REGISTRY


def reset_registry() -> None:
    global _REGISTRY
    _REGISTRY = None
