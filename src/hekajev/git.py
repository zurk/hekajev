import os
import subprocess
from pathlib import Path
from threading import Timer

from hekajev.config import Limits


class GitError(ValueError):
    pass


class Repository:
    def __init__(self, path: Path):
        self.path = path.resolve()
        self.run("rev-parse", "--git-dir")
        bare = self.run("rev-parse", "--is-bare-repository").strip() == "true"
        self.path = Path(
            self.run("rev-parse", "--absolute-git-dir" if bare else "--show-toplevel").strip()
        )
        self.head = self.run("rev-parse", "HEAD").strip()
        self.shallow = self.run("rev-parse", "--is-shallow-repository").strip() == "true"

    def command(self, *args: str, literal_paths: bool = True) -> list[str]:
        options = ["--literal-pathspecs"] if literal_paths else []
        return ["git", "--no-pager", *options, "-C", str(self.path), *args]

    def run(self, *args: str, literal_paths: bool = True) -> str:
        proc = subprocess.run(
            self.command(*args, literal_paths=literal_paths), capture_output=True, timeout=60
        )
        if proc.returncode:
            # Git errors can contain credential-bearing remote URLs or arbitrary file contents.
            raise GitError(f"Git {args[0]} failed (exit {proc.returncode}) in {self.path.name}")
        return proc.stdout.decode("utf-8", errors="replace")

    def commits(
        self,
        revision: str,
        limit: int,
        since: str | None,
        until: str | None,
        *,
        include_merges: bool = False,
        path_patterns: list[str] | None = None,
    ) -> list[str]:
        if revision.startswith("-"):
            raise ValueError("Revision cannot start with '-'")
        args = ["log", "--format=%H", f"--max-count={limit}"]
        if not include_merges:
            args.append("--no-merges")
        if since is not None:
            args.append(f"--since={since}")
        if until is not None:
            args.append(f"--until={until}")
        if path_patterns:
            args.extend(
                [
                    "--full-history",
                    "--root",
                    "--no-renames",
                    "--diff-filter=ACDMRTUXB",
                    "--diff-merges=first-parent",
                    "--no-patch",
                ]
            )
        return self.run(
            *args,
            revision,
            "--",
            *self.pathspecs(path_patterns),
            literal_paths=not path_patterns,
        ).splitlines()

    @staticmethod
    def pathspecs(patterns: list[str] | None) -> list[str]:
        if any(not pattern or pattern.startswith("/") for pattern in patterns or []):
            raise ValueError("Path patterns must be nonempty and relative to the repository root")
        return [f":(top,glob){pattern}" for pattern in patterns or []]

    def matches_paths(self, sha: str, patterns: list[str]) -> bool:
        return bool(
            self.run(
                "show",
                "--format=",
                "--name-only",
                "-z",
                "--root",
                "--no-renames",
                "--diff-merges=first-parent",
                sha,
                "--",
                *self.pathspecs(patterns),
                literal_paths=False,
            )
        )

    def patch(self, parent: str, sha: str, paths: list[str], limit: int) -> tuple[str, bool]:
        args = self.command(
            "diff",
            "--no-ext-diff",
            "--no-textconv",
            "--no-color",
            "--find-renames",
            "--unified=3",
            parent,
            sha,
            "--",
            *paths,
        )
        # Read a bounded pipe; a generated one-line file must not exhaust Python memory.
        with subprocess.Popen(args, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL) as proc:
            assert proc.stdout is not None
            deadline = Timer(60, proc.kill)
            deadline.daemon = True
            deadline.start()
            try:
                data = proc.stdout.read(limit + 1)
                truncated = len(data) > limit
                if truncated:
                    proc.kill()
                code = proc.wait(timeout=60)
            finally:
                deadline.cancel()
                if proc.poll() is None:
                    proc.kill()
                    proc.wait()
            if code and not truncated:
                raise GitError(f"Git diff failed (exit {code}) for {sha}")
        return data[:limit].decode("utf-8", errors="ignore"), truncated

    def commit(self, sha: str, limits: Limits, *, include_merges: bool = False) -> dict:
        meta = self.run("show", "-s", "--format=%H%x00%P%x00%cI%x00%B", sha).split("\0", 3)
        parents = meta[1].split()
        if len(parents) > 1 and not include_merges:
            raise GitError("Merge commit requires --include-merges")
        if parents:
            parent = parents[0]
        else:
            if self.shallow:
                raise GitError("Shallow boundary has no parent; fetch more history")
            parent = self.run("hash-object", "-t", "tree", os.devnull).strip()
        names = self.run("diff", "--name-status", "-z", "--find-renames", parent, sha, "--")
        tokens = iter(names.rstrip("\0").split("\0") if names else [])
        files = []
        for change in tokens:
            old = next(tokens)
            if change.startswith(("R", "C")):
                files.append({"status": change, "path": next(tokens), "old_path": old})
            else:
                files.append({"status": change, "path": old})
        files.sort(key=lambda file: file["path"])
        patches = []
        remaining = limits.input_bytes * limits.max_chunks
        for index, file in enumerate(files):
            if remaining <= 0:
                break
            paths = [file["path"]]
            if "old_path" in file:
                paths.append(file["old_path"])
            quota = max(1, remaining // (len(files) - index))
            patch, truncated = self.patch(parent, sha, paths, quota)
            remaining -= len(patch.encode("utf-8"))
            patches.append({"path": file["path"], "diff": patch, "truncated": truncated})
        # Small later files leave capacity that earlier truncated patches can still use.
        for file, patch in zip(files, patches, strict=False):
            if remaining <= 0:
                break
            if not patch["truncated"]:
                continue
            paths = [file["path"]]
            if "old_path" in file:
                paths.append(file["old_path"])
            previous_size = len(patch["diff"].encode("utf-8"))
            diff, truncated = self.patch(parent, sha, paths, previous_size + remaining)
            remaining -= len(diff.encode("utf-8")) - previous_size
            patch.update(diff=diff, truncated=truncated)
        omitted = len(files) - len(patches)
        return {
            "sha": sha,
            "parent": parent,
            "parents": parents,
            "is_merge": len(parents) > 1,
            "date": meta[2],
            "title": meta[3].splitlines()[0] if meta[3].strip() else "",
            "message": meta[3].strip(),
            "files": files,
            "patches": patches,
            "omitted_patch_files": omitted,
            "truncated": omitted > 0 or any(patch["truncated"] for patch in patches),
        }
