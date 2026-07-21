"""P3 capability catalog, grants, deterministic Broker, and shadow metrics."""

from .names import *
from .contracts import *
from .state import *
from .catalog import ToolPolicy, build_tool_catalog, schemas_by_name
from .argument_validation import (
    JSON_SCHEMA_DIALECT,
    ToolArgumentValidationError,
    ToolArgumentValidator,
    ToolSchemaValidationError,
)
from .broker import CapabilityBroker
from .shadow import (
    BROKER_SHADOW_RESULT_FORMAT,
    BrokerShadowEvaluator,
    BrokerShadowResult,
    NecessaryToolExpectation,
)
from .legacy_shadow import (
    BROKER_LEGACY_SHADOW_FORMAT,
    LegacyBrokerShadowObserver,
    attach_legacy_broker_shadow,
    build_legacy_broker_shadow_observer,
    detach_legacy_broker_shadow,
)
from .registry import (
    CapabilityOwner,
    RuntimeCapabilityRegistry,
    legacy_local_capability_registry,
)

__all__ = [name for name in globals() if not name.startswith("_")]
