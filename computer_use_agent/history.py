# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Keep useful desktop observations in the model's tool history."""

import re
from collections import defaultdict
from hashlib import sha256
from itertools import pairwise

_HISTORY = "computer_use_observations"
_OMITTED = "[An identical full tree is retained in a later tool reply.]"
_APP_HEADER = re.compile(r'^(?:App: "|Window \d+[:,])')
_FULL_TREE = re.compile(r"^Tree: full$", re.MULTILINE)


def _digest(text):
    return sha256(text.encode("utf-8", "surrogatepass")).hexdigest()


def remember_observations(message, result):
    if message is None or not isinstance(message.content, str) or result.get("isError"):
        return
    blocks = result.get("content", ())
    if any(block["type"] not in ("text", "image") for block in blocks):
        return
    text = "\n\n".join(block["text"] for block in blocks if block["type"] == "text")
    if not text or (message.content != text and not message.content.startswith(text + "\n\n")):
        return
    observations, offset = [], 0
    for block in blocks:
        if block["type"] == "text":
            body = block["text"]
            end = offset + len(body)
            if _APP_HEADER.match(body) and _FULL_TREE.search(body):
                observations.append((offset, end, _digest(body)))
            offset = end + 2
    if observations:
        message.metadata[_HISTORY] = {"digest": _digest(message.content), "observations": observations}


def compact_observations(messages):
    """Omit exact duplicate full trees while retaining distinct baselines and all diffs."""
    full_texts = set()
    changed = False
    for index in range(len(messages) - 1, -1, -1):
        message = messages[index]
        history = message.metadata.get(_HISTORY) if message.role == "tool" else None
        if not history or not isinstance(message.content, str) or _digest(message.content) != history["digest"]:
            continue
        content = message.content
        retained, replacements = [], []
        shift = 0
        for start, end, digest in history["observations"]:
            if digest in full_texts:
                header = content[start:end].partition("\n")[0]
                replacement = header + "\n" + _OMITTED
                replacements.append((start, end, replacement))
                shift += len(replacement) - (end - start)
            else:
                retained.append((start + shift, end + shift, digest))
        # The SDK can keep separate diff baselines for different handles of
        # the same displayed window. Window numbers cannot identify a baseline.
        full_texts.update(digest for _, _, digest in retained)
        if replacements:
            for start, end, replacement in reversed(replacements):
                content = content[:start] + replacement + content[end:]
            metadata = message.metadata.copy()
            if retained:
                metadata[_HISTORY] = {"digest": _digest(content), "observations": retained}
            else:
                metadata.pop(_HISTORY)
            messages[index] = message.model_copy(update={"content": content, "metadata": metadata})
            changed = True
    return changed


def compact_screenshots(messages, image_scopes):
    """Retain each scope's latest batch and reuse identical preceding captures."""
    scoped_batches = defaultdict(list)
    for index in range(1, len(messages)):
        message = messages[index]
        # DeepAgent adds tool images in a user message after the tool reply.
        if message.role != "user" or messages[index - 1].role != "tool" or not isinstance(message.content, list):
            continue
        by_scope = defaultdict(list)
        for position, (label, image) in enumerate(pairwise(message.content), start=1):
            if not isinstance(label, dict) or label.get("type") != "text":
                continue
            if not isinstance(image, dict) or image.get("type") != "image_url":
                continue
            text, image_url = label.get("text"), image.get("image_url")
            capture = image_scopes.get(text) if isinstance(text, str) else None
            if capture is not None and isinstance(image_url, dict) and image_url.get("url") == capture[1]:
                by_scope[capture[0]].append((index, position, text, capture[1]))
        for scope, batch in by_scope.items():
            scoped_batches[scope].append(batch)

    retained, changes = {}, defaultdict(dict)
    for scope, batches in scoped_batches.items():
        keep = len(batches) - 1
        latest = batches[keep]
        if len(latest) == 1:
            for previous in reversed(batches[:keep]):
                if len(previous) != 1 or previous[0][3] != latest[0][3]:
                    break
                keep -= 1
        for position, batch in enumerate(batches):
            for index, block, label, url in batch:
                if position == keep:
                    retained[label] = (scope, url)
                elif position > keep:
                    original = batches[keep][0][2].partition("\n")[0]
                    changes[index][block] = f"[Screenshot unchanged from {original}.]"
                else:
                    changes[index][block] = "[Screenshot omitted from history after a newer capture of the same scope.]"
    for index, replacements in changes.items():
        message = messages[index]
        blocks = message.content.copy()
        for position, note in replacements.items():
            blocks[position] = {"type": "text", "text": note}
        messages[index] = message.model_copy(update={"content": blocks})
    return retained, bool(changes)
