"""Credential and vault CLI operations. Presentation data never contains values."""

from __future__ import annotations

import getpass
import json
import sys
from functools import partial

from ..credentials.profile_store import plugin_name, reference_name
from ..credentials.vault import CredentialVault
from ..credentials.vault_crypto import FILE_LIMIT, SECRET_LIMIT, decode
from ..exceptions import ConfigurationError, VaultError
from ..storage.path_safety import canonical_path
from ..storage.transactions import finish_transaction
from .constants import EXIT_SUCCESS
from .outcome import CommandOutcome
from .prepared import CredentialArguments, PreparedCommand, VaultArguments
from .setup import _config_for


def _result(outcome: CommandOutcome, payload: dict[str, object]) -> int:
    return outcome.set_result(
        payload, EXIT_SUCCESS, text=lambda: print(json.dumps(payload, ensure_ascii=False, indent=2))
    )


def _stdin(limit: int) -> str:
    stream = getattr(sys.stdin, "buffer", sys.stdin)
    value = stream.read(limit + 1)
    if isinstance(value, str):
        value = value.encode("utf-8")
    if len(value) > limit:
        raise VaultError("size")
    try:
        return value.decode("utf-8")
    except UnicodeError:
        raise VaultError("input") from None


def _prompt(label: str) -> str:
    if not sys.stdin.isatty():
        raise ConfigurationError("secret input requires an interactive terminal or the explicit stdin option")
    return getpass.getpass(label)


class CredentialCommandHandler:
    async def handle(self, command: PreparedCommand, outcome: CommandOutcome) -> int:
        args = command.arguments
        assert isinstance(args, CredentialArguments)
        reference = args.credential_reference
        plugin = args.credential_plugin_id
        if args.credential_action != "list":
            reference_name(reference or "")
            if args.credential_kind == "plugin":
                plugin_name(plugin or "")
        config, _, _, _ = _config_for(args, None)
        store = CredentialVault.from_config(config).profile(config.profile.default)
        kind, action = args.credential_kind, args.credential_action
        if action == "list":
            entries = await finish_transaction(partial(store.list_entries, kind))
            return _result(outcome, {"profile": store.profile, "entries": entries})
        assert reference is not None
        if action == "delete":
            if not args.yes:
                description = f"delete {kind} credential {plugin or ''} {reference} from profile {store.profile}"
                if not sys.stdin.isatty() or input(description + "? [yes/no] ").strip().lower() != "yes":
                    raise ConfigurationError("credential deletion requires confirmation or --yes")
            removed = await finish_transaction(partial(store.delete, kind, reference, plugin))
            return _result(outcome, {"profile": store.profile, "deleted": removed})
        if kind == "plugin":
            value = _stdin(SECRET_LIMIT) if args.secret_stdin else _prompt("Secret: ")
            assert plugin is not None
            await finish_transaction(partial(store.set_plugin, plugin, reference, value))
        else:
            if args.secret_stdin:
                pair = decode(_stdin(FILE_LIMIT).encode("utf-8"))
                if not isinstance(pair, dict) or set(pair) != {"username", "password"}:
                    raise VaultError("input")
                username, password = pair["username"], pair["password"]
            else:
                username, password = _prompt("SMTP username: "), _prompt("SMTP password: ")
            await finish_transaction(partial(store.set_smtp, reference, username, password))
        return _result(outcome, {"profile": store.profile, "stored": True})


class VaultCommandHandler:
    async def handle(self, command: PreparedCommand, outcome: CommandOutcome) -> int:
        args = command.arguments
        assert isinstance(args, VaultArguments)
        target = args.vault_target
        if target is not None:
            target = canonical_path(target, "vault backup path")
        config, _, _, _ = _config_for(args, None)
        vault = CredentialVault.from_config(config)
        action = args.vault_action
        if action == "status":
            return _result(outcome, await finish_transaction(vault.status))
        if action == "init":
            await finish_transaction(vault.initialize)
        elif action == "rotate-key":
            await finish_transaction(partial(vault.rotate_key, resume=args.resume))
        else:
            assert target is not None
            passphrase = (
                _stdin(SECRET_LIMIT).removesuffix("\n").removesuffix("\r")
                if args.passphrase_stdin
                else _prompt("Passphrase: ")
            )
            if not args.passphrase_stdin and action == "backup" and _prompt("Confirm passphrase: ") != passphrase:
                raise VaultError("input")
            operation = vault.backup if action == "backup" else vault.restore
            await finish_transaction(partial(operation, target, passphrase))
        return _result(outcome, {"operation": "vault", "action": action, "completed": True})
