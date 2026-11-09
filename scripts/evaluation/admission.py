# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Artifact admission bridge; operator-supplied review is never auto-generated.

Development summaries only assign used parameter fingerprints. Final outcomes
never enter the review packet. All admission attempts reserve a slot before UI
reset, including attempts that later fail or never deliver a summary.
"""

import argparse
import json
from pathlib import Path

from computer_use.programs.admission import Attempt, Evidence, HumanReview, Registry, fingerprint
from computer_use.programs.artifact import Artifact

from ..console import report
from .ledger import EnvironmentBlocked, enumerate_episodes
from .provenance import digest


def instance_fingerprint(metadata):
    # Dataset authors supply semantic instance values; random reset/episode
    # identifiers are excluded by the dataset contract, not guessed by task name.
    return fingerprint({"task": metadata["task"], "parameters": metadata["instance_parameters"]})


def seed_development(registry: Registry, runs: Path, *, skip_episode=None):
    if not runs.exists():
        return
    for summary in enumerate_episodes(runs):
        if summary["episode_id"] == skip_episode:
            continue
        metadata = summary.get("metadata")
        if metadata is None:
            raise ValueError("Prior incomplete/corrupt episode needs reconciliation before allocating new splits")
        if metadata.get("split") == "development":
            registry.assign_instance(metadata["task"], metadata["instance_sha256"], "development")


def prepare_episode(registry: Registry, episode, artifact: Artifact) -> bool:
    """Register before reset; return whether a result must fill a reserved slot."""
    from dataclasses import asdict

    metadata, program = episode.metadata, artifact.definition
    split, instance_sha256 = metadata["split"], metadata["instance_sha256"]
    if metadata["program"] != program:
        raise ValueError("Episode does not describe the selected artifact")
    registry.assign_instance(metadata["task"], instance_sha256, split)
    episode.record("instance_assignment", instance_sha256=instance_sha256, split=split)
    if split == "final":
        selection = registry.select(program)
        episode.record("program_selection", selection=asdict(selection))
        episode.require_log()
        if not selection.allowed:
            raise EnvironmentBlocked("Program admission denied: " + selection.reason)
    else:
        registry.register(program)
    if split == "admission":
        registry.reserve_attempt(
            Attempt(
                episode.id,
                program["id"],
                program["version"],
                program["implementation_sha256"],
                metadata["task"],
                instance_sha256,
                metadata["expected_acceptance"],
            )
        )
        episode.record("admission_reserved", instance_sha256=instance_sha256)
    episode.require_log()
    return split == "admission"


def record_summary(registry: Registry, path: Path):
    raw = path.read_bytes()
    summary = json.loads(raw)
    metadata = summary["metadata"]
    if metadata["split"] != "admission" or metadata["arm"] != "program":
        raise ValueError("Only program admission episodes may be attached")
    program, runtime = metadata["program"], metadata["runtime"]
    registry.record_evidence(
        Evidence(
            episode_id=summary["episode_id"],
            program_id=program["id"],
            program_version=program["version"],
            implementation_sha256=program["implementation_sha256"],
            task=metadata["task"],
            instance_sha256=metadata["instance_sha256"],
            split=metadata["split"],
            summary_sha256=digest(raw),
            source_sha256=runtime["source"]["source_sha256"],
            driver_sha256=runtime["driver"]["sha256"],
            recording_complete=summary["recording_complete"],
            outcome=summary["outcome"],
            acceptance=summary["acceptance"]["value"],
            expected_acceptance=metadata["expected_acceptance"],
        )
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--registry", type=Path, required=True)
    parser.add_argument("--artifact", type=Path, required=True)
    commands = parser.add_subparsers(dest="command", required=True)
    register = commands.add_parser("register")
    register.add_argument("--development-runs", type=Path, required=True)
    commands.add_parser("packet")
    admit = commands.add_parser("admit")
    admit.add_argument("--human-review", type=Path, required=True)
    withdraw = commands.add_parser("withdraw")
    withdraw.add_argument("--state", choices=("invalidated", "retired"), required=True)
    withdraw.add_argument("--reason", required=True)
    args = parser.parse_args()
    registry, program = Registry(args.registry), Artifact.read(args.artifact).definition
    if args.command == "register":
        registry.register(program)
        seed_development(registry, args.development_runs)
    elif args.command == "admit":
        # The calling host/operator must authenticate this human authorization.
        # An implementation request or a model-generated review is insufficient.
        review = HumanReview(**json.loads(args.human_review.read_text()))
        registry.admit(program["id"], program["version"], review)
    elif args.command == "withdraw":
        registry.withdraw(program["id"], program["version"], state=args.state, reason=args.reason)
    packet = registry.review_packet(program["id"], program["version"])
    report(json.dumps(packet, ensure_ascii=False, indent=2), raise_errors=True)


if __name__ == "__main__":
    main()
