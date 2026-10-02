# Maintaining CLI definitions and configuration

Register new options in `commands/options.py`. `OptionDefinition` owns the name,
destination, type conversion, default, repeat behavior, choices, command scope,
and no-op metadata. Add exclusions to `CONFLICT_GROUPS`. The parser reads these
definitions once, regardless of the option's position in the command hierarchy.
The validation indexes are derived at module import; this is not a runtime plugin
registration or hot-reload API.

The registry controls acceptance, not feature implementation. A new option also
needs a command consumer. Type conversion alone does not enforce a positive
number, non-empty string, safe absolute path, or existence of a file. Define
those rules in common validation before configuration resolution. A no-op entry
must be honored by its consumer and applicable value validation.

When adding a choice to an existing option, update its consumers and related
configuration/runtime contracts together. For example, an inspection level
needs an implementation in the inspection renderer; an image format needs
configuration, save-option, and processing support. Adding a `choices` entry
alone can pass argument validation and fail later, or select unintended behavior.

`test_choice_contracts.py` evaluates every declared output, inspection, and
fallback choice against its consumer and checks the verification/workflow type
contracts. These tests deliberately fail when an advertised choice has no
corresponding contract or behavior. They complement feature-specific tests;
schema acceptance does not prove download or image-processing success.

Removing an option makes its former spelling an unknown option. Remove or update
its execution consumers, compatibility attributes, specialized validation, help,
examples, and tests together. Common validation tolerates absent optional
definitions, but that does not make remaining execution references safe. Rename
an option's consumers and canonical validation references as well as its registry
entry; an explicit `dest` can preserve the Namespace attribute when appropriate.

When reducing or renaming choices, old explicit values must be rejected and the
diagnostic must list only current choices. Update defaults too: argparse and
Pydantic do not necessarily validate omitted defaults against their choice lists.
The choice contract tests check declared defaults, a configuration-default
roundtrip, and the bundled YAML against the current schema. CLI choices may be
a subset of the library's choices; the contract tests allow that intentional
restriction while continuing to reject advertised values the consumer lacks.

Choose an explicit migration policy before removing configuration fields.
`validate_config`/direct model input rejects unknown fields, while user-managed
YAML resolution prunes obsolete/unknown static keys before validation and can
remove them from disk when `rewrite_user_layers=True`. Read-only resolution does
not rewrite the file. Changing a field's choices or type rejects invalid old
values, but no automatic value renaming or migration is provided. Check bundled
defaults, commented templates, overlays, and runtime overrides when changing
the schema or its constraints.

Add configuration fields and permitted values to the Pydantic models in
`configuration/models.py`. Layer sanitizing derives its fields from that schema,
and diagnostics now derive field names from the same model instead of a separate
name allowlist. Aliases and new numeric bounds are covered by extension tests.
Diagnostic field traversal stops at user-defined mapping keys, even when a key
coincides with a real schema field. Never publish arbitrary validator exceptions;
new custom constraints need explicit, safe core diagnostic conditions.

For each extension, verify placement before/within/after the hierarchy, explicit
defaults, repeated values, invalid types/choices, scope/conflicts, JSON/text
output, help, and `--`. Invalid inputs and help must not enter configuration or
execution. Update the CLI/configuration documentation and add tests for the
feature's actual effect. Manually constructed legacy Namespaces cannot recover
whether a value equal to its default was explicitly supplied; parsed Namespaces
retain that information.

See `tests/v3/cli/test_definition_extensions.py` for temporary definitions and
the [extension verification report](../../investigations/cli-arguments-2026-10-03/extension-verification.md)
for the checked cases and execution limits.

Removal and change cases are in `tests/v3/cli/test_definition_reductions.py` and
the [reduction/change verification report](../../investigations/cli-arguments-2026-10-03/reduction-verification.md).
