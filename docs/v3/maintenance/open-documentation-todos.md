# Open documentation and policy TODOs

This is the intentionally small backlog left after the API v3 reference audit.
It records questions that cannot be settled from this repository's reference
documentation, example source units, or implementation.  It is **not** a
second API specification: the current stable contracts remain the documents
linked from [the v3 index](../README.md).

Each item needs an external service, a publisher/operator decision, or a
maintainer policy before it can be closed.  Do not infer an answer from a
fixture host, a generated signing key, or the current implementation.

| ID | priority | unresolved question | why repository material cannot answer it | decision or artifact needed | owner | status |
| --- | --- | --- | --- | --- | --- | --- |
| DOC-EXT-001 | P1 | What URL and plugin can a new user use for a real end-to-end successful download? | Tutorials and local source units deliberately use `example.test` fixture hosts; they are not a public service guarantee. | A maintained local demo fixture, or a published support catalog with ownership and availability policy. | product / plugin maintainers | open |
| DOC-EXT-002 | P1 | Which publisher key or third-party plugin is trustworthy for a particular deployment? | Strict verification proves agreement with a configured catalog pin; it cannot prove real-world publisher identity, key custody, or organization approval. | Key-distribution, review, revocation, and deployment trust policy owned by the operator. | deployment operator / security owner | open |
| DOC-EXT-003 | P2 | How long are API v3 and plugin package contracts supported, and how are breaking changes deprecated? | `__all__` defines the present stable surface, but source code cannot establish a future compatibility or release-notice commitment. | A versioning, deprecation, and release-note policy. | project maintainers | open |
| DOC-EXT-004 | P2 | What controlled vocabulary, if any, may consumers assign to manifest `capabilities`? | The current contract intentionally accepts informational strings and grants no permission; it defines no interoperable vocabulary or policy semantics. | A registered vocabulary and consumer interpretation policy, or an explicit decision to keep the field opaque. | project / catalog maintainers | open |
| DOC-EXT-005 | P2 | Which browser, profile, encryption setup, and OS combinations are supported by browser-cookie import? | The feature delegates browser access to an optional third-party dependency and depends on the user's local browser state. | A tested support matrix and support policy, maintained alongside the optional dependency. | project maintainers | open |

Until an item is decided, consumer documentation must state only the current
framework boundary: use a site plugin selected for the target URL, install only
plugins trusted by the deployment operator, treat `capabilities` as
informational, and use browser import on a best-effort basis.  The following
existing documents remain the current behavior references:

- [CLI reference](../reference/cli.md)
- [Plugin package and trust reference](../reference/plugin-package.md)
- [Plugin hook reference](../reference/plugin-hooks.md)
- [Library API reference](../reference/library-api.md)
