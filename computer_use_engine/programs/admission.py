# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Local, explicit program admission. No execution, discovery or target bindings.

The host authenticates the human reviewer. This store validates and retains the
reviewed artifact/evidence, not the identity of whoever can edit a local file.
SQLite owns lifecycle and split assignment atomically; contention fails rather
than retrying. Programs still execute through their existing SDK composition.
"""

import hashlib
import json
import re
import sqlite3
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal


def canonical(value) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def fingerprint(value) -> str:
    """Hash semantic parameters, excluding random episode IDs/session references."""
    return hashlib.sha256(canonical(value).encode()).hexdigest()


def _sha(value):
    if not isinstance(value, str) or re.fullmatch(r"[0-9a-f]{64}", value) is None:
        raise ValueError("A lowercase SHA-256 digest is required")


def _definition(value):
    required = {"id", "version", "implementation_sha256", "acceptance", "constraints"}
    if set(value) - required - {"state"} or not required <= set(value):
        raise ValueError("Unsupported program definition")
    if any(not isinstance(value[k], str) or not value[k].strip() for k in required):
        raise ValueError("Program definition fields must be nonempty strings")
    if value.get("state", "candidate") != "candidate":
        raise ValueError("A source definition cannot grant itself admission")
    _sha(value["implementation_sha256"])
    return {key: value[key] for key in sorted(required)}


@dataclass(frozen=True)
class Attempt:
    episode_id: str
    program_id: str
    program_version: str
    implementation_sha256: str
    task: str
    instance_sha256: str
    expected_acceptance: Literal["TRUE", "FALSE"]

    def __post_init__(self):
        _sha(self.implementation_sha256)
        _sha(self.instance_sha256)
        if not all(
            (self.episode_id, self.program_id, self.program_version, self.task)
        ) or self.expected_acceptance not in ("TRUE", "FALSE"):
            raise ValueError("Invalid admission attempt")


@dataclass(frozen=True)
class Evidence:
    episode_id: str
    program_id: str
    program_version: str
    implementation_sha256: str
    task: str
    instance_sha256: str
    split: Literal["development", "admission", "final"]
    summary_sha256: str
    source_sha256: str
    driver_sha256: str
    recording_complete: bool
    outcome: str
    acceptance: Literal["TRUE", "FALSE", "UNKNOWN"]
    expected_acceptance: Literal["TRUE", "FALSE"]

    def __post_init__(self):
        for key, value in asdict(self).items():
            if key.endswith("sha256"):
                _sha(value)
        if not all(
            (self.episode_id, self.program_id, self.program_version, self.task, self.outcome)
        ) or self.split not in ("development", "admission", "final"):
            raise ValueError("Invalid episode evidence")
        if (
            self.acceptance not in ("TRUE", "FALSE", "UNKNOWN")
            or self.expected_acceptance not in ("TRUE", "FALSE")
            or not isinstance(self.recording_complete, bool)
        ):
            raise ValueError("Invalid episode evidence")


@dataclass(frozen=True)
class HumanReview:
    reviewer: str
    authorization_reference: str
    packet_sha256: str
    decision: Literal["approve"] = "approve"

    def __post_init__(self):
        _sha(self.packet_sha256)
        if not self.reviewer.strip() or not self.authorization_reference.strip() or self.decision != "approve":
            raise ValueError("An explicit human approval and its source are required")


@dataclass(frozen=True)
class Selection:
    allowed: bool
    reason: str
    state: str | None
    definition_sha256: str
    review: dict | None = None


class Registry:
    """One lifecycle owner. Invalidated/retired versions cannot be re-admitted."""

    def __init__(self, path: Path):
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        path.touch(mode=0o600, exist_ok=True)
        with self._transaction() as db:
            if db.execute("PRAGMA user_version").fetchone()[0] not in (0, 1):
                raise ValueError("Unsupported admission registry schema")
            db.execute("""CREATE TABLE IF NOT EXISTS versions (
                id TEXT, version TEXT, definition TEXT NOT NULL, state TEXT NOT NULL,
                review TEXT, PRIMARY KEY(id, version))""")
            db.execute("""CREATE TABLE IF NOT EXISTS instances (
                task TEXT, fingerprint TEXT, split TEXT NOT NULL, PRIMARY KEY(task, fingerprint))""")
            db.execute("""CREATE TABLE IF NOT EXISTS evidence (
                episode_id TEXT PRIMARY KEY, id TEXT, version TEXT, plan TEXT NOT NULL, content TEXT,
                FOREIGN KEY(id, version) REFERENCES versions(id, version))""")
            db.execute("""CREATE TABLE IF NOT EXISTS history (
                sequence INTEGER PRIMARY KEY, id TEXT, version TEXT, at TEXT,
                state TEXT, reason TEXT, actor TEXT)""")
            db.execute("PRAGMA user_version = 1")

    @contextmanager
    def _transaction(self):
        db = sqlite3.connect(self.path, timeout=0)
        db.row_factory = sqlite3.Row
        try:
            db.execute("PRAGMA foreign_keys = ON")
            db.execute("BEGIN IMMEDIATE")
            yield db
            db.commit()
        except BaseException:
            db.rollback()
            raise
        finally:
            db.close()

    @staticmethod
    def _history(db, key, state, reason, actor):
        db.execute(
            "INSERT INTO history(id,version,at,state,reason,actor) VALUES(?,?,?,?,?,?)",
            (*key, datetime.now(timezone.utc).isoformat(), state, reason, actor),
        )

    def register(self, definition: dict):
        value = _definition(definition)
        key, content = (value["id"], value["version"]), canonical(value)
        with self._transaction() as db:
            previous = db.execute("SELECT definition FROM versions WHERE id=? AND version=?", key).fetchone()
            if previous:
                if previous["definition"] != content:
                    raise ValueError("Program versions are immutable; use a new version")
                return
            db.execute("INSERT INTO versions VALUES(?,?,?,'candidate',NULL)", (*key, content))
            self._history(db, key, "candidate", "registered immutable definition", "host")

    def assign_instance(self, task: str, instance_sha256: str, split: str):
        _sha(instance_sha256)
        if not task or split not in ("development", "admission", "final"):
            raise ValueError("A task and explicit dataset split are required")
        with self._transaction() as db:
            previous = db.execute(
                "SELECT split FROM instances WHERE task=? AND fingerprint=?", (task, instance_sha256)
            ).fetchone()
            if previous and previous["split"] != split:
                raise ValueError("The same semantic instance cannot cross dataset splits")
            db.execute("INSERT OR IGNORE INTO instances VALUES(?,?,?)", (task, instance_sha256, split))

    def reserve_attempt(self, attempt: Attempt):
        """Call before reset/input so interrupted attempts cannot disappear."""
        key = (attempt.program_id, attempt.program_version)
        with self._transaction() as db:
            version = db.execute("SELECT * FROM versions WHERE id=? AND version=?", key).fetchone()
            if not version or version["state"] != "candidate":
                raise ValueError("Attempts may only be attached to a registered candidate")
            if json.loads(version["definition"])["implementation_sha256"] != attempt.implementation_sha256:
                raise ValueError("Evidence belongs to another implementation")
            assigned = db.execute(
                "SELECT split FROM instances WHERE task=? AND fingerprint=?", (attempt.task, attempt.instance_sha256)
            ).fetchone()
            if not assigned or assigned["split"] != "admission":
                raise ValueError("Instance must be assigned to admission before reserving an attempt")
            previous = db.execute("SELECT plan FROM evidence WHERE episode_id=?", (attempt.episode_id,)).fetchone()
            plan = canonical(asdict(attempt))
            if previous:
                raise ValueError("An episode attempt cannot be reserved twice")
            db.execute("INSERT INTO evidence VALUES(?,?,?,?,NULL)", (attempt.episode_id, *key, plan))

    def record_evidence(self, evidence: Evidence):
        # Final test outcomes must never enter an admission/optimization packet.
        if evidence.split != "admission":
            raise ValueError("Only admission evidence may enter a review packet")
        with self._transaction() as db:
            previous = db.execute(
                "SELECT plan,content FROM evidence WHERE episode_id=?", (evidence.episode_id,)
            ).fetchone()
            if not previous:
                raise ValueError("Reserve an attempt before executing it")
            values = asdict(evidence)
            if any(values[key] != value for key, value in json.loads(previous["plan"]).items()):
                raise ValueError("Evidence does not match the reserved attempt")
            content = canonical(asdict(evidence))
            if previous["content"] is not None and previous["content"] != content:
                raise ValueError("An episode's evidence cannot be overwritten")
            db.execute("UPDATE evidence SET content=? WHERE episode_id=?", (content, evidence.episode_id))

    @staticmethod
    def _packet(db, key):
        version = db.execute("SELECT * FROM versions WHERE id=? AND version=?", key).fetchone()
        if not version:
            raise KeyError(key)
        evidence = [
            {"attempt": json.loads(row["plan"]), "result": json.loads(row["content"]) if row["content"] else None}
            for row in db.execute("SELECT plan,content FROM evidence WHERE id=? AND version=? ORDER BY episode_id", key)
        ]
        return {"definition": json.loads(version["definition"]), "evidence": evidence}

    def review_packet(self, program_id: str, version: str):
        with self._transaction() as db:
            packet = self._packet(db, (program_id, version))
        return {**packet, "packet_sha256": fingerprint(packet)}

    def admit(self, program_id: str, version: str, review: HumanReview):
        key = (program_id, version)
        with self._transaction() as db:
            current = db.execute("SELECT state FROM versions WHERE id=? AND version=?", key).fetchone()
            if not current or current["state"] != "candidate":
                raise ValueError("Only a candidate may be admitted")
            packet = self._packet(db, key)
            if fingerprint(packet) != review.packet_sha256:
                raise ValueError("Review does not cover the current definition and complete evidence set")
            evidence = [item["result"] for item in packet["evidence"]]
            if not evidence or not all(row and row["recording_complete"] for row in evidence):
                raise ValueError("Admission requires complete records, including failures")
            if any(row["expected_acceptance"] == "FALSE" and row["acceptance"] == "TRUE" for row in evidence):
                raise ValueError("False acceptance blocks admission")
            if not any(row["outcome"] == "success" and row["acceptance"] == "TRUE" for row in evidence):
                raise ValueError("Admission requires an independently successful instance")
            if not any(row["expected_acceptance"] == "FALSE" and row["acceptance"] == "FALSE" for row in evidence):
                raise ValueError("Admission requires a rejected independent negative instance")
            db.execute(
                "UPDATE versions SET state='admitted',review=? WHERE id=? AND version=?",
                (canonical(asdict(review)), *key),
            )
            self._history(db, key, "admitted", review.authorization_reference, review.reviewer)

    def withdraw(self, program_id: str, version: str, *, state: str, reason: str):
        if state not in ("invalidated", "retired") or not reason.strip():
            raise ValueError("Withdrawal requires a state and reason")
        key = (program_id, version)
        with self._transaction() as db:
            current = db.execute("SELECT state FROM versions WHERE id=? AND version=?", key).fetchone()
            if not current or current["state"] == "retired":
                raise ValueError("Invalid lifecycle transition")
            if current["state"] == "invalidated" and state != "retired":
                raise ValueError("Invalid lifecycle transition")
            db.execute("UPDATE versions SET state=? WHERE id=? AND version=?", (state, *key))
            self._history(db, key, state, reason, "host")

    def select(self, definition: dict) -> Selection:
        """Gate before execution. Drift invalidation is durable before returning."""
        value = _definition(definition)
        key, digest = (value["id"], value["version"]), fingerprint(value)
        with self._transaction() as db:
            row = db.execute("SELECT * FROM versions WHERE id=? AND version=?", key).fetchone()
            if not row:
                return Selection(False, "not_registered", None, digest)
            if row["definition"] != canonical(value):
                if row["state"] in ("candidate", "admitted"):
                    db.execute("UPDATE versions SET state='invalidated' WHERE id=? AND version=?", key)
                    self._history(db, key, "invalidated", "definition changed", "selection_gate")
                return Selection(
                    False, "definition_changed", "retired" if row["state"] == "retired" else "invalidated", digest
                )
            state = row["state"]
            return Selection(
                state == "admitted",
                "admitted" if state == "admitted" else "not_admitted",
                state,
                digest,
                json.loads(row["review"]) if row["review"] else None,
            )
