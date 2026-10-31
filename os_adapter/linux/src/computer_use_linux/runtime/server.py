# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""One protocol thread owns sequencing, request cache and cancellation."""

import collections
import hashlib
import json
import logging
import queue
import sys
import threading
import time

from computer_use_linux.contracts.protocol import Failure, Request, fields, integer, require, token

_LOG = logging.getLogger(__name__)


class Server:
    """One native execution thread; the reader only queues frames and cancels."""

    def __init__(self, factory, source=None, output=None):
        self.factory = factory
        self.source = source or sys.stdin.buffer
        self.output = output or sys.stdout
        self.session = token("linux")
        self.stopped = threading.Event()
        self.inbox = queue.Queue(maxsize=64)
        self.pending = {}
        self.pending_lock = threading.Lock()
        self.cache = collections.OrderedDict()
        self.high_id = 0
        self.backend = None
        self.version = None
        self.native = None
        self.fatal = None

    def reply(self, request_id, **value):
        self.output.write(
            json.dumps(
                dict(version=self.version or 6, session=self.session, id=request_id, **value),
                ensure_ascii=True,
                allow_nan=False,
                separators=(",", ":"),
            )
            + "\n"
        )
        self.output.flush()

    def _reader(self):
        try:
            while not self.stopped.is_set():
                line = self.source.readline(4 * 1024 * 1024 + 1)
                if not line:
                    break
                if len(line) > 4 * 1024 * 1024 or not line.endswith(b"\n") or line.startswith(b"\xef\xbb\xbf"):
                    raise ValueError("invalid or oversized frame")

                def pairs(items):
                    result = {}
                    for key, value in items:
                        if key in result:
                            raise ValueError("duplicate JSON field")
                        result[key] = value
                    return result

                raw = json.loads(
                    line.decode("utf-8"),
                    object_pairs_hook=pairs,
                    parse_constant=lambda _: (_ for _ in ()).throw(ValueError("nonfinite JSON")),
                )
                if not isinstance(raw, dict):
                    raise ValueError("expected object")
                if "cancel" in raw:
                    fields(raw, ("version", "session", "cancel"))
                    require(
                        # Exact types preserve scalar validation and identity contracts.
                        # pylint: disable-next=unidiomatic-typecheck
                        type(raw["version"]) is int
                        and raw["version"] == self.version
                        and raw["session"] == self.session,
                        "Invalid cancellation session.",
                    )
                    integer(raw["cancel"], 1)
                    with self.pending_lock:
                        for ctx in self.pending.get(raw["cancel"], []):
                            ctx.cancelled.set()
                    continue
                integer(raw.get("id"))
                timeout = raw.get("timeoutMs", 30000)
                integer(timeout, 1, 120000)
                ctx = Request(time.monotonic() + timeout / 1000, self.stopped)
                with self.pending_lock:
                    self.pending.setdefault(raw["id"], []).append(ctx)
                self.inbox.put_nowait((raw, ctx))
        except Exception as error:
            self.fatal = f"protocol: {error}"
        finally:
            self.stopped.set()

    def run(self):
        threading.Thread(target=self._reader, name="stdio-reader", daemon=True).start()
        try:
            while not self.stopped.is_set() or not self.inbox.empty():
                try:
                    raw, ctx = self.inbox.get(timeout=0.025)
                except queue.Empty:
                    if self.native:
                        self.native.expire()
                    if self.backend:
                        self.backend.pump()
                    continue
                try:
                    self.execute(raw, ctx)
                finally:
                    with self.pending_lock:
                        contexts = self.pending.get(raw["id"], [])
                        if ctx in contexts:
                            contexts.remove(ctx)
                        if not contexts:
                            self.pending.pop(raw["id"], None)
                if self.fatal:
                    break
        finally:
            try:
                if self.native:
                    self.native.close()
            finally:
                if self.backend:
                    self.backend.close()
        if self.fatal:
            _LOG.error("%s", self.fatal)
            return 1
        return 0

    def execute(self, raw, ctx):
        identifier = raw["id"]
        fingerprint = hashlib.sha256(json.dumps(raw, sort_keys=True).encode()).digest()
        accepted = False
        try:
            require(
                # pylint: disable-next=unidiomatic-typecheck
                type(raw.get("version")) is int
                and raw["version"] == 6
                and (self.version is None or raw["version"] == self.version),
                "This Linux driver requires protocol 6; upgrade the client instead of using legacy commands.",
            )
            if identifier == 0:
                fields(raw, ("version", "id", "method", "params"))
                require(self.backend is None and raw["method"] == "hello", "Expected one hello.")
                fields(raw["params"], ("desktopMode",))
                require(raw["params"]["desktopMode"] == "exclusive", "Invalid desktop mode.")
                ctx.check()
                self.version = raw["version"]
                self.backend = self.factory(raw["params"]["desktopMode"], self.stopped)
                from computer_use_linux.runtime.native import NativeExecution

                self.native = NativeExecution(self.backend, self.session)
                self.reply(
                    0,
                    result=dict(
                        platform="linux",
                        desktopMode=raw["params"]["desktopMode"],
                        maxTimeoutMs=120000,
                        capabilities=self.native.capabilities,
                    ),
                )
                return
            fields(raw, ("version", "session", "id", "method", "params", "timeoutMs"))
            require(self.backend is not None and raw["session"] == self.session, "Invalid session.")
            if identifier in self.cache:
                prior, reply = self.cache[identifier]
                require(prior == fingerprint, "Request id reused with different content.", "request_conflict")
                self.reply(identifier, **reply)
                return
            require(identifier > self.high_id, "Request id expired; it will not execute again.", "request_conflict")
            self.high_id = identifier
            accepted = True
            ctx.check()
            ctx.pump = self.backend.pump
            result = self.native.perform(raw["method"], raw["params"], ctx)
            reply = dict(result=result)
        except Failure as error:
            if error.target == "desktop":
                error.target = ctx.target
            if ctx.effect != "none":
                error.effect = ctx.effect
            failure = error.wire()
            if ctx.context:
                failure["context"] = ctx.context
            reply = dict(error=failure)
        except Exception as error:
            _LOG.error("request %s: %s: %s", identifier, type(error).__name__, error)
            failure = Failure(str(error) or type(error).__name__, target=ctx.target, effect=ctx.effect).wire()
            if ctx.context:
                failure["context"] = ctx.context
            reply = dict(error=failure)
        if accepted and identifier not in self.cache:
            self.cache[identifier] = (fingerprint, reply)
            while len(self.cache) > 256:
                self.cache.popitem(last=False)
        self.reply(identifier, **reply)
