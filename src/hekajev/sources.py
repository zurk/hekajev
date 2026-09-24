import hashlib
import logging
import os
import re
import shutil
import subprocess
import tempfile
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import quote, unquote, urlsplit

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class Source:
    id: str
    name: str
    path: Path
    origin: str | None
    identity: str


@dataclass(frozen=True)
class _Remote:
    url: str
    identity: str
    name: str
    origin: str | None


class SourceError(ValueError):
    pass


def default_cache_dir() -> Path:
    configured = os.environ.get("XDG_CACHE_HOME")
    root = Path(configured).expanduser() if configured else Path.home() / ".cache"
    if not root.is_absolute():
        root = Path.home() / ".cache"
    return root / "hekajev"


def _remote(value: str, *, strip_credentials: bool = False) -> _Remote:
    if not value or value.startswith("-") or any(ord(char) < 33 for char in value):
        raise SourceError("Use a local path, HTTPS URL, or SSH Git URL")
    scp = re.fullmatch(r"(?:(?P<user>[\w.-]+)@)?(?P<host>[\w.-]+):(?P<path>[^:].*)", value)
    relative = False
    try:
        if "://" in value:
            parsed = urlsplit(value)
            scheme, host, port = parsed.scheme, parsed.hostname, parsed.port
            user, password = parsed.username, parsed.password
            path = unquote(parsed.path)
            if parsed.query or parsed.fragment:
                raise SourceError("Repository URLs cannot contain queries or fragments")
            if scheme not in {"https", "ssh"}:
                raise SourceError("Only HTTPS and SSH repository URLs are supported")
            if scheme == "https" and (user is not None or password is not None):
                if not strip_credentials:
                    raise SourceError("Use Git authentication, not credentials in repository URLs")
                user, password = None, None
            if password is not None:
                raise SourceError("SSH repository URLs cannot contain passwords")
        elif scp:
            scheme, port, password = "ssh", None, None
            host, user, path = scp.group("host", "user", "path")
            path = unquote(path)
            relative = not path.startswith("/")
        else:
            raise SourceError("Use a local path, HTTPS URL, or SSH Git URL")
    except ValueError as exc:
        if isinstance(exc, SourceError):
            raise
        raise SourceError("Invalid repository URL") from None
    if not host or not re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9.-]*", host):
        raise SourceError("Invalid repository host")
    if user is not None and not re.fullmatch(r"[a-zA-Z0-9_][a-zA-Z0-9_.-]*", user):
        raise SourceError("Invalid SSH repository username")
    if not path or any(not (char.isalnum() or char in "/-._~") for char in path):
        raise SourceError("Invalid repository path in URL")
    path = path.rstrip("/")
    segments = path.lstrip("/").split("/")
    if not segments or any(part in {"", ".", ".."} for part in segments):
        raise SourceError("Invalid repository path in URL")
    if relative and segments[0].startswith("-"):
        raise SourceError("Repository paths cannot be remote command options")
    host = host.lower()
    repo_path = path.removesuffix(".git")
    name = repo_path.rsplit("/", 1)[-1]
    if not name:
        raise SourceError("Repository URL must identify a repository")
    authority = host + (f":{port}" if port is not None else "")
    if scheme == "ssh" and user:
        authority = f"{user}@{authority}"
    encoded = quote(path, safe="/-._~")
    url = f"{authority}:{encoded}" if relative else f"{scheme}://{authority}/{encoded.lstrip('/')}"
    origin = None
    default_port = 443 if scheme == "https" else 22
    if host in {"github.com", "gitlab.com"} and port in {None, default_port}:
        public_path = repo_path.lstrip("/")
        origin = f"https://{host}/{quote(public_path, safe='/-._~')}"
        identity = f"remote:{host}/{public_path.lower() if host == 'github.com' else public_path}"
    else:
        identity = f"remote:{scheme}:{authority}:{'relative:' if relative else '/'}{repo_path}"
    return _Remote(url, identity, name, origin)


def _git(*args: str, operation: str, timeout: int = 600, check: bool = True) -> str:
    # Restrict URL rewrites too: a supported URL must not activate a Git protocol helper.
    command = [
        "git",
        "-c",
        "protocol.allow=never",
        "-c",
        "protocol.https.allow=always",
        "-c",
        "protocol.ssh.allow=always",
        *args,
    ]
    try:
        result = subprocess.run(command, capture_output=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        raise SourceError(f"{operation} timed out") from None
    except OSError:
        raise SourceError(f"{operation} could not run Git") from None
    if result.returncode and check:
        # Git errors can include credentials from config, authentication, or URL rewrites.
        raise SourceError(f"{operation} failed (Git exit {result.returncode})")
    return result.stdout.decode("utf-8", errors="replace").strip() if not result.returncode else ""


def _origin(path: Path) -> str | None:
    raw = _git(
        "-C",
        str(path),
        "config",
        "--get",
        "remote.origin.url",
        operation="Reading repository origin",
        check=False,
    )
    try:
        return _remote(raw, strip_credentials=True).origin
    except SourceError:
        return None


@contextmanager
def _cache_lock(path: Path) -> Iterator[None]:
    # Kernel locks release after a crash; a lock-file's existence never means it is held.
    with path.open("a+b") as lock:
        if os.name == "nt":
            import msvcrt
            import time

            lock.seek(0, os.SEEK_END)
            if not lock.tell():
                lock.write(b"\0")
                lock.flush()
            lock.seek(0)
            while True:
                try:
                    msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
                    break
                except OSError:
                    time.sleep(0.1)
            try:
                yield
            finally:
                lock.seek(0)
                msvcrt.locking(lock.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl

            fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(lock.fileno(), fcntl.LOCK_UN)


def prepare_source(source: str, cache_dir: Path, *, refresh: bool = True) -> Source:
    if not source or source.startswith("-") or any(ord(char) < 32 for char in source):
        raise SourceError("Use a local path, HTTPS URL, or SSH Git URL")
    remote = _remote(source) if "://" in source else None
    candidate = Path(source).expanduser()
    if remote is None and candidate.is_dir():
        bare = (
            _git(
                "-C",
                str(candidate),
                "rev-parse",
                "--is-bare-repository",
                operation="Inspecting local repository",
            )
            == "true"
        )
        root = _git(
            "-C",
            str(candidate),
            "rev-parse",
            "--absolute-git-dir" if bare else "--show-toplevel",
            operation="Inspecting local repository",
        )
        path = Path(root).resolve()
        identity = f"local:{path}"
        source_id = hashlib.sha256(identity.encode()).hexdigest()[:16]
        return Source(source_id, path.name.removesuffix(".git"), path, _origin(path), identity)
    remote = remote or _remote(source)
    source_id = hashlib.sha256(remote.identity.encode()).hexdigest()[:16]
    cache_dir = cache_dir.expanduser().resolve()
    cache_dir.mkdir(parents=True, exist_ok=True)
    path = cache_dir / f"{source_id}.git"
    with _cache_lock(cache_dir / f"{source_id}.lock"):
        if path.exists():
            bare = _git(
                "-C",
                str(path),
                "rev-parse",
                "--is-bare-repository",
                operation="Inspecting cached repository",
            )
            raw = _git(
                "-C",
                str(path),
                "config",
                "--get",
                "remote.origin.url",
                operation="Inspecting cached repository",
            )
            if bare != "true" or _remote(raw).identity != remote.identity:
                raise SourceError("Cached repository does not match the requested source")
            if refresh:
                logger.info("Refreshing repository %s", remote.name)
                _git(
                    "-C",
                    str(path),
                    "remote",
                    "set-url",
                    "origin",
                    remote.url,
                    operation="Updating cached repository origin",
                )
                _git(
                    "-C",
                    str(path),
                    "fetch",
                    "--quiet",
                    "--prune",
                    "origin",
                    "+refs/heads/*:refs/heads/*",
                    "+refs/tags/*:refs/tags/*",
                    operation="Refreshing repository",
                )
                refs = _git(
                    "-C",
                    str(path),
                    "ls-remote",
                    "--symref",
                    "origin",
                    "HEAD",
                    operation="Reading repository default branch",
                )
                for line in refs.splitlines():
                    if line.startswith("ref: refs/heads/") and line.endswith("\tHEAD"):
                        head = line.removeprefix("ref: ").removesuffix("\tHEAD")
                        _git(
                            "-C",
                            str(path),
                            "symbolic-ref",
                            "HEAD",
                            head,
                            operation="Updating cached default branch",
                        )
                        break
        else:
            logger.info("Cloning repository %s", remote.name)
            temporary = Path(tempfile.mkdtemp(prefix=f".{source_id}-", dir=cache_dir))
            try:
                _git(
                    "clone",
                    "--bare",
                    "--quiet",
                    "--",
                    remote.url,
                    str(temporary),
                    operation="Cloning repository",
                )
                temporary.rename(path)
            finally:
                if temporary.exists():
                    shutil.rmtree(temporary)
    return Source(source_id, remote.name, path, remote.origin, remote.identity)
