# Testing and documentation contracts

<a id="testing-config-cli"></a>
<a id="testing-package-trust"></a>
<a id="testing-migration"></a>
<a id="testing-runtime-library"></a>
<a id="testing-release"></a>

The v3 suite checks configuration acceptance, CLI dispatch/JSON/redaction, plugin package trust, lifecycle/auth/recovery, transport/persistence safety, result/exception behavior, and source processor examples.

Documentation contract tests additionally check:

- facade `__all__` and inventory order;
- [public signature index](../reference/api-signatures.md) の visible `api-contract` marker、parameter names/kinds/defaults、coroutine status、DTO/Pydantic field names、enum/literal values、and `ERROR_CATALOG`;
- parser option acceptance/effect/no-op/rejection and JSON payload shapes;
- legacy/current claim IDs, destination markers, source snapshot evidence, and `superseded` rationale;
- recursive current-documentation links. Archive is not a current-link target.

Pydantic inherited API and private names are excluded from the custom stable-method inventory. A change to public behavior must update the implementation, canonical reference, contract marker, and behavior test together.
