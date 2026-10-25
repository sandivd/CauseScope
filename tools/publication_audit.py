"""Read-only, bounded publication checks. A clean scan is not a safety guarantee."""
from __future__ import annotations

import argparse
import base64
import gzip
import hashlib
import io
import json
import os
import re
import stat
import tarfile
import zipfile
from pathlib import Path, PurePosixPath


ROOT = Path(__file__).resolve().parents[1]
MAX_FILE_BYTES = 32 * 1024 * 1024
MAX_ARCHIVE_BYTES = 128 * 1024 * 1024
MAX_MEMBERS = 5000
MAX_DEPTH = 3
METADATA = {
    "PKG-INFO", "src/causescope.egg-info/PKG-INFO",
    "src/causescope.egg-info/SOURCES.txt",
    "src/causescope.egg-info/dependency_links.txt",
    "src/causescope.egg-info/entry_points.txt",
    "src/causescope.egg-info/requires.txt",
    "src/causescope.egg-info/top_level.txt", "setup.cfg",
}
PRIVATE_NAMES = {
    "ground-truth.json", "ground_truth.json", "screening.json",
    "transcript.json", "transcripts.json", "observations.sqlite",
    "native-observations.jsonl.gz", "reproduction-commands.json",
    "id_rsa", "id_ed25519", "credentials",
}
PRIVATE_PARTS = {
    "evidence", "private", ".private-evaluation", "participant-responses",
    "__pycache__", ".git", ".aws", ".causescope", "transcripts", "raw", "logs",
    "research", "participant-feedback",
}
PATTERNS = {
    "private_key": re.compile(r"-----BEGIN (?:RSA |EC |DSA |OPENSSH |ENCRYPTED )?PRIVATE KEY-----"),
    "recognizable_token": re.compile(
        r"(?<![A-Za-z0-9])(?:sk-(?:proj-|svcacct-)?[A-Za-z0-9_-]{20,}"
        r"|gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{30,}"
        r"|xox[baprs]-[A-Za-z0-9-]{15,}|AKIA[A-Z0-9]{16}|AIza[A-Za-z0-9_-]{30,})"),
    "authentication_header": re.compile(
        r"(?i)(?:authorization|proxy-authorization)[\"']?\s*[:=]\s*[\"']?"
        r"(?:Bearer|Basic)\s+[A-Za-z0-9+/_.=-]{8,}"),
    "credential_literal": re.compile(
        r"(?im)[\"']?(?:api[_-]?key|access[_-]?token|refresh[_-]?token|client[_-]?secret"
        r"|password|passwd|aws_secret_access_key)[\"']?\s*[:=]\s*[\"']?"
        r"[^\s\"',}\]]{8,}"),
    "credential_url": re.compile(
        r"(?i)(?:https?|postgres(?:ql)?|mysql|mongodb|redis)://[^\s/:@]{1,80}:[^\s/@]{2,80}@"),
    "jwt": re.compile(r"eyJ[A-Za-z0-9_-]{12,}\.eyJ[A-Za-z0-9_-]{12,}\.[A-Za-z0-9_-]{12,}"),
    "unix_personal_path": re.compile(r"(?<!:)/(?:Users|home)/[A-Za-z0-9_.-]+/[^\s\"'<>]+"),
}
WINDOWS_PATH = re.compile(r"(?<![A-Za-z0-9])[A-Za-z]:[\\/]+[^\s\"'<>]+")
PORTABLE_PREFIXES = ("c:/path/", "c:/your/", "c:/windows/", "x:/")
# Local verification outputs are never part of the public source snapshot.
LOCAL_DIRECTORIES = {"results", "dist", "build", ".tools", ".causescope", "htmlcov"}
LOCAL_CACHE_DIRECTORIES = {"__pycache__", ".pytest_cache"}


def policy(root=ROOT):
    value = json.loads((Path(root) / "publication-files.json").read_text(encoding="utf-8"))
    paths = value["sdist"] + value["source_additions"]
    if len(paths) != len(set(paths)) or any(unsafe_path(p) for p in paths):
        raise ValueError("Publication policy must use unique relative paths")
    return value


def unsafe_path(name):
    normalized = name.replace("\\", "/")
    return (normalized.startswith("/") or bool(re.match(r"^[A-Za-z]:", normalized))
            or ".." in PurePosixPath(normalized).parts)


def private_path(name):
    path = PurePosixPath(name.replace("\\", "/").lower())
    return (path.name in PRIVATE_NAMES or bool(set(path.parts) & PRIVATE_PARTS)
            or path.name.startswith(('ground-truth.', 'ground_truth.', 'transcript.', 'transcripts.'))
            or any(p == '.venv' or p.startswith('.venv-') for p in path.parts)
            or path.name == ".env" or path.name.startswith(".env.")
            or path.suffix in {".pem", ".key", ".p12", ".pfx"})


class Audit:
    def __init__(self, root=ROOT):
        self.root = Path(root)
        self.policy = policy(root)
        self.findings = []
        self.files = []
        self._seen = set()
        self._bytes = 0
        self.excluded_local_paths = []

    def finding(self, path, rule, line=None):
        key = (path, rule, line)
        if key not in self._seen:
            self._seen.add(key)
            self.findings.append({"path": path, "rule": rule, "line": line,
                                  "matched_values": "omitted"})

    def text(self, name, text, *, decode=True):
        # Normalize JSON-escaped backslashes without evaluating any input.
        normalized = text.replace("\\\\", "\\").replace("\\\\", "\\")
        for rule, pattern in PATTERNS.items():
            for match in pattern.finditer(normalized):
                self.finding(name, rule, normalized.count("\n", 0, match.start()) + 1)
        for match in WINDOWS_PATH.finditer(normalized):
            value = match.group().replace("\\", "/").lower()
            escaped_newline = len(value) == 4 and value[3] in {"n", "r", "t"}
            if value != "c:/windows" and not escaped_newline and not value.startswith(PORTABLE_PREFIXES):
                self.finding(name, "absolute_local_path", normalized.count("\n", 0, match.start()) + 1)
        if decode:
            try:
                self.json_fields(name, json.loads(text))
            except (ValueError, RecursionError):
                pass

    def json_fields(self, name, value, depth=0):
        if depth > 50:
            self.finding(name, "json_depth_limit")
            return
        if isinstance(value, dict):
            for key, item in value.items():
                if key.endswith("_base64") and isinstance(item, str):
                    try:
                        payload = base64.b64decode(item, validate=True)
                    except ValueError:
                        self.finding(name, "invalid_base64_evidence")
                    else:
                        self.payload(name + "#" + key, payload, decode=False)
                self.json_fields(name, item, depth + 1)
        elif isinstance(value, list):
            for item in value:
                self.json_fields(name, item, depth + 1)

    def payload(self, name, raw, *, decode=True):
        self._bytes += len(raw)
        if len(raw) > MAX_FILE_BYTES or self._bytes > MAX_ARCHIVE_BYTES:
            self.finding(name, "scan_size_limit")
            return
        self.text(name, raw.decode("utf-8-sig", errors="replace"), decode=decode)

    def source(self, *, require_no_vcs=False):
        approved = set(self.policy["sdist"] + self.policy["source_additions"])
        # Inspect the whole tree, not just the allowlist. Do not follow links or
        # junctions, including links in paths normally reserved for local output.
        for directory, dirs, files in os.walk(self.root, followlinks=False):
            for entry in list(dirs) + files:
                path = Path(directory) / entry
                name = path.relative_to(self.root).as_posix()
                info = path.lstat()
                if path.is_symlink() or getattr(info, 'st_file_attributes', 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT:
                    self.finding(name, "source_link")
                    if entry in dirs:
                        dirs.remove(entry)
                elif entry in {".git", ".hg", ".svn"}:
                    if require_no_vcs or Path(directory) != self.root:
                        self.finding(name, "version_control_metadata")
                    else:
                        self.excluded_local_paths.append(name)
                    if entry in dirs:
                        dirs.remove(entry)
                elif entry in dirs and (entry in LOCAL_CACHE_DIRECTORIES or
                        (Path(directory) == self.root and (entry in LOCAL_DIRECTORIES or
                         entry == '.venv' or entry.startswith('.venv-'))) or entry.endswith('.egg-info')):
                    self.excluded_local_paths.append(name + "/")
                    dirs.remove(entry)
                elif entry in files and Path(directory) == self.root and entry == '.coverage':
                    self.excluded_local_paths.append(name)
                elif entry in files and name not in approved:
                    self.finding(name, "unreviewed_source_file")
        for name in self.policy["sdist"] + self.policy["source_additions"]:
            path = self.root / name
            if not path.resolve().is_relative_to(self.root.resolve()):
                self.finding(name, "source_path_escape")
                continue
            if private_path(name):
                self.finding(name, "private_source_path")
            try:
                if path.stat().st_size > MAX_FILE_BYTES:
                    self.finding(name, "scan_size_limit")
                    continue
                raw = path.read_bytes()
            except OSError:
                self.finding(name, "unreadable_source")
                continue
            self.files.append({"path": name, "sha256": hashlib.sha256(raw).hexdigest()})
            self.payload(name, raw)

    def archive(self, path, kind):
        path = Path(path)
        try:
            if path.stat().st_size > MAX_ARCHIVE_BYTES:
                self.finding(path.name, "archive_size_limit")
                return
            raw = path.read_bytes()
            self.files.append({"path": path.name, "sha256": hashlib.sha256(raw).hexdigest()})
            self.members(path.name, raw, kind)
        except (OSError, ValueError, EOFError, tarfile.TarError, zipfile.BadZipFile, RuntimeError):
            self.finding(path.name, "unreadable_archive")

    def members(self, label, raw, kind, depth=0):
        if depth > MAX_DEPTH:
            self.finding(label, "archive_depth_limit")
            return
        rows = []
        if kind == "sdist":
            with tarfile.open(fileobj=io.BytesIO(raw), mode="r:gz") as archive:
                members = archive.getmembers()
                if len(members) > MAX_MEMBERS or sum(m.size for m in members) > MAX_ARCHIVE_BYTES:
                    self.finding(label, "archive_size_limit")
                    return
                roots = {m.name.split("/")[0] for m in members}
                if len(roots) != 1:
                    self.finding(label, "sdist_root_layout")
                for member in members:
                    if unsafe_path(member.name) or member.issym() or member.islnk():
                        self.finding(label + "#" + member.name, "unsafe_archive_member")
                    if not member.isfile():
                        continue
                    if member.size > MAX_FILE_BYTES:
                        self.finding(label + "#" + member.name, "scan_size_limit")
                        continue
                    name = member.name.partition("/")[2]
                    rows.append((name, archive.extractfile(member).read()))
        else:
            with zipfile.ZipFile(io.BytesIO(raw)) as archive:
                members = archive.infolist()
                if len(members) > MAX_MEMBERS or sum(m.file_size for m in members) > MAX_ARCHIVE_BYTES:
                    self.finding(label, "archive_size_limit")
                    return
                for member in members:
                    if unsafe_path(member.filename) or (member.external_attr >> 16) & 0o170000 == 0o120000:
                        self.finding(label + "#" + member.filename, "unsafe_archive_member")
                    if member.is_dir():
                        continue
                    if member.file_size > MAX_FILE_BYTES:
                        self.finding(label + "#" + member.filename, "scan_size_limit")
                        continue
                    rows.append((member.filename, archive.read(member)))
        present = {name for name, _ in rows}
        if len(present) != len(rows):
            self.finding(label, 'duplicate_archive_members')
        if kind == "sdist":
            for name in set(self.policy["sdist"]) - present:
                self.finding(label + "#" + name, "missing_sdist_member")
        if kind == 'participant' and 'MANIFEST.json' in present:
            try:
                manifest = json.loads(next(data for name, data in rows if name == 'MANIFEST.json'))
                expected = manifest['files_sha256']
                if set(expected) != present - {'MANIFEST.json'} or any(
                        hashlib.sha256(data).hexdigest() != expected.get(name)
                        for name, data in rows if name != 'MANIFEST.json'):
                    self.finding(label, 'participant_manifest_mismatch')
            except (ValueError, KeyError, TypeError, AttributeError):
                self.finding(label, 'invalid_participant_manifest')
        for name, payload in rows:
            location = label + "#" + name
            if private_path(name):
                self.finding(location, "private_archive_member")
            if kind == "sdist" and name not in set(self.policy["sdist"]) | METADATA:
                self.finding(location, "unapproved_sdist_member")
            if kind == "wheel":
                allowed = {p.removeprefix("src/") for p in self.policy["sdist"] if p.startswith("src/")}
                metadata = re.fullmatch(r"causescope-[^/]+\.dist-info/(?:METADATA|WHEEL|RECORD|entry_points\.txt|top_level\.txt|licenses/LICENSE)", name)
                if name not in allowed and not metadata:
                    self.finding(location, "unapproved_wheel_member")
            if kind == "participant":
                top = name.split("/")[0]
                if top not in {"task", "wheelhouse", "README.md", "MCP.md", "QUICKSTART.md", "setup.py", "MANIFEST.json", "feedback-template.json"}:
                    self.finding(location, "unapproved_participant_member")
                if name == "feedback-template.json":
                    try:
                        feedback = json.loads(payload)
                        if feedback.get("status") != "template_not_observations" or any(
                                v not in (None, [], "template_not_observations") for v in feedback.values()):
                            self.finding(location, "participant_observations")
                    except (ValueError, AttributeError):
                        self.finding(location, "invalid_feedback_template")
            if name.endswith((".zip", ".whl")):
                try:
                    nested_kind = "wheel" if PurePosixPath(name).name.startswith("causescope-") else "dependency"
                    self.members(location, payload, nested_kind, depth + 1)
                except (ValueError, EOFError, zipfile.BadZipFile, RuntimeError):
                    self.finding(location, "unreadable_archive")
            elif name.endswith(".gz"):
                try:
                    with gzip.GzipFile(fileobj=io.BytesIO(payload)) as stream:
                        self.payload(location, stream.read(MAX_FILE_BYTES + 1))
                except (OSError, EOFError):
                    self.finding(location, "unreadable_gzip")
            else:
                self.payload(location, payload)

    def report(self):
        return {"schema_version": 1, "scope": self.policy["scope"],
                "git_index_verified": False, "passed": not self.findings,
                "excluded_local_paths": sorted(self.excluded_local_paths),
                "limitations": ["Recognizable patterns only; clean scans do not guarantee safety.",
                                "Working-tree scope and supplied archives only; no Git index inspection.",
                                "No OS isolation or publisher authenticity is established by this scan."],
                "files_sha256": self.files, "findings": self.findings}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", action="store_true")
    parser.add_argument("--require-no-vcs", action="store_true",
                        help="Fail if source contains version-control metadata (standalone snapshot check)")
    parser.add_argument("--sdist", action="append", type=Path, default=[])
    parser.add_argument("--wheel", action="append", type=Path, default=[])
    parser.add_argument("--participant-zip", action="append", type=Path, default=[])
    parser.add_argument("--output", type=Path)
    args = parser.parse_args(argv)
    if not (args.source or args.sdist or args.wheel or args.participant_zip):
        parser.error("Choose --source and/or an archive")
    audit = Audit()
    if args.source:
        audit.source(require_no_vcs=args.require_no_vcs)
    for kind, paths in (("sdist", args.sdist), ("wheel", args.wheel), ("participant", args.participant_zip)):
        for path in paths:
            audit.archive(path, kind)
    report = audit.report()
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        with args.output.open("x", encoding="utf-8") as stream:
            json.dump(report, stream, indent=2)
            stream.write("\n")
    print(json.dumps(report, indent=2))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
