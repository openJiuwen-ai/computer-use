# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Multiline terminal input and cancellable redirected input."""

import asyncio
import os
import stat
import sys


class MessageInput:
    def __init__(self, *, stream=None, interactive=None):
        self.stream = stream or sys.stdin
        self.interactive = self.stream.isatty() and sys.stdout.isatty() if interactive is None else interactive
        self.session = None
        self.reader = self.transport = None
        self._previous_keys = {}
        if self.interactive:
            from prompt_toolkit import PromptSession
            from prompt_toolkit.history import InMemoryHistory
            from prompt_toolkit.input.vt100_parser import ANSI_SEQUENCES
            from prompt_toolkit.key_binding import KeyBindings
            from prompt_toolkit.keys import Keys

            keys = KeyBindings()

            @keys.add("c-m")
            def submit(event):
                event.current_buffer.validate_and_handle()

            @keys.add("c-j")
            @keys.add("escape", "c-m")
            def newline(event):
                event.current_buffer.insert_text("\n")

            @keys.add("c-d")
            def eof(event):
                if event.current_buffer.text:
                    event.current_buffer.delete()
                else:
                    event.app.exit(exception=EOFError())

            self.session = PromptSession(multiline=True, history=InMemoryHistory(), key_bindings=keys)
            for sequence in ("\x1b[13;2u", "\x1b[27;2;13~"):
                self._previous_keys[sequence] = ANSI_SEQUENCES.get(sequence)
                ANSI_SEQUENCES[sequence] = Keys.ControlJ

    async def read(self):
        if self.session is not None:
            return await self.session.prompt_async("你> ")
        if self.reader is not None:
            raw = await self.reader.readline()
            value = raw.decode(getattr(self.stream, "encoding", None) or "utf-8", "replace")
        elif os.name == "posix":
            try:
                descriptor = self.stream.fileno()
                if stat.S_ISREG(os.fstat(descriptor).st_mode):
                    value = self.stream.readline()
                else:
                    if self.reader is None:
                        self.reader = asyncio.StreamReader()
                        self.transport, _ = await asyncio.get_running_loop().connect_read_pipe(
                            lambda: asyncio.StreamReaderProtocol(self.reader),
                            self.stream,
                        )
                    raw = await self.reader.readline()
                    value = raw.decode(getattr(self.stream, "encoding", None) or "utf-8", "replace")
            except (OSError, ValueError):
                value = self.stream.readline()
        else:
            from anyio import to_thread

            value = await to_thread.run_sync(self.stream.readline, abandon_on_cancel=True)
        if not value:
            raise EOFError
        return value.rstrip("\r\n")

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc_value, traceback):
        if self.transport is not None:
            self.transport.close()
        if self._previous_keys:
            from prompt_toolkit.input.vt100_parser import ANSI_SEQUENCES

            for key, previous in self._previous_keys.items():
                if previous is None:
                    ANSI_SEQUENCES.pop(key, None)
                else:
                    ANSI_SEQUENCES[key] = previous
