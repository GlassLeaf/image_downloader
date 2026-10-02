# CLI diagnostics changes (unreleased)

CLI syntax, command applicability, conflicts, and invalid CLI values now return
`argument_error`, `exception="ArgumentError"`, and a specific safe `message`.
Configuration-file failures remain `configuration_error`. Both exit with status 2.
There is no compatibility mode for the former argument-error code.

Normal errors use stderr: `error [code]: message`. With `--json`, stdout contains
exactly one `{"error": {...}}` object. The required fields remain `code`, `reason`,
`exception`, `message`, and `operation`. `reason` is stable category text;
`message` describes the specific problem. Optional `details` has a required
`kind` and applicable `option`, `options`, `command`, `argument`, `choices`,
`field`, `source`, `line`, and `column`. It never contains option values, full
input, absolute paths, YAML excerpts, or credentials. A command not established
by parsing uses `operation="cli"`; otherwise it uses its canonical top-level name.
Unknown external exceptions keep safe generic messages.

Consumers should change their argument-failure branch:

```python
if result["error"]["code"] == "argument_error":
    show_input_problem(result["error"]["message"])
elif result["error"]["code"] == "configuration_error":
    show_configuration_problem(result["error"]["message"])
```

Option abbreviations are no longer accepted. Replace `--j` with `--json`, and
use `--list-updated-urls` instead of the former abbreviation `--list`.
`help [COMMAND [SUBCOMMAND ...]]` is a text help alias, even with JSON enabled.
Unknown words no longer implicitly become download URLs. Use an absolute HTTP(S)
URL with a host. Successful JSON, existing exit codes, documented no-op options,
and the legacy Cookie options retain their contracts.

See the [CLI reference](../reference/cli.md) and
[public error catalog](../reference/library-api.md#api-errors).

For adding options, choices, or configuration fields, see
[definition extensions](cli-definition-extensions.md).
