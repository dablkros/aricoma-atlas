"""Configurable host-key Netmiko transport with stable, secret-free errors."""

from pathlib import Path
from typing import Iterator

import paramiko
from netmiko import ConnectHandler
from netmiko.exceptions import (
    NetmikoAuthenticationException,
    NetmikoTimeoutException,
    ReadException,
    ReadTimeout,
)

from atlas.network.base import ConnectionContext
from atlas.network.errors import (
    AuthenticationFailedError,
    CommandFailedError,
    ConnectionFailedError,
    ConnectionTimeoutError,
    HostKeyMismatchError,
    HostKeyUnknownError,
)


def _exception_chain(exc: Exception) -> Iterator[BaseException]:
    seen = set()
    current: BaseException | None = exc
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        yield current
        current = current.__cause__ or current.__context__


def _host_key_failure(exc: Exception) -> str | None:
    for item in _exception_chain(exc):
        if isinstance(item, paramiko.BadHostKeyException):
            return "mismatch"
        if (
            isinstance(item, paramiko.SSHException)
            and "not found in known_hosts" in str(item)
        ):
            return "unknown"
    return None


class NetmikoTransport:
    """Execute one command per connection; never persist or log command output."""

    def __init__(self, connector=ConnectHandler) -> None:
        self.connector = connector

    @staticmethod
    def _validate_known_hosts(path: Path) -> None:
        if path.is_symlink() or not path.is_file():
            raise HostKeyUnknownError()

    def run_command(
        self,
        context: ConnectionContext,
        *,
        device_type: str,
        command: str,
        use_enable: bool = False,
    ) -> str:
        if context.strict_host_keys:
            self._validate_known_hosts(context.known_hosts_file)
        connection = None
        try:
            connection = self.connector(
                device_type=device_type,
                host=context.host,
                username=context.username,
                password=context.password,
                secret=context.enable_password or "",
                allow_agent=False,
                use_keys=False,
                ssh_strict=context.strict_host_keys,
                system_host_keys=False,
                alt_host_keys=context.strict_host_keys,
                alt_key_file=str(context.known_hosts_file),
                conn_timeout=context.timeouts.connect,
                auth_timeout=context.timeouts.authentication,
                banner_timeout=context.timeouts.connect,
                read_timeout_override=context.timeouts.command,
            )
        except NetmikoAuthenticationException:
            raise AuthenticationFailedError() from None
        except Exception as exc:
            host_key_failure = _host_key_failure(exc)
            if host_key_failure == "mismatch":
                raise HostKeyMismatchError() from None
            if host_key_failure == "unknown":
                raise HostKeyUnknownError() from None
            if isinstance(exc, NetmikoTimeoutException):
                raise ConnectionTimeoutError() from None
            if isinstance(exc, (OSError, paramiko.SSHException)):
                raise ConnectionFailedError() from None
            raise

        try:
            if use_enable and context.enable_password:
                connection.enable()
            output = connection.send_command(
                command,
                read_timeout=context.timeouts.command,
            )
            if not isinstance(output, str) or not output.strip():
                raise CommandFailedError()
            return output
        except (ReadException, ReadTimeout, NetmikoTimeoutException):
            raise CommandFailedError() from None
        except CommandFailedError:
            raise
        except Exception:
            raise CommandFailedError() from None
        finally:
            try:
                connection.disconnect()
            except Exception:
                pass


__all__ = ["NetmikoTransport"]
