# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Local model endpoint exercising the real DeepAgent and MCP stacks."""

import json
import threading
from collections import deque
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

pytest.importorskip("openjiuwen")


def tool_call(code):
    return {
        "role": "assistant",
        "content": None,
        "tool_calls": [
            {
                "id": "call_computer",
                "type": "function",
                "function": {
                    "name": "computer_use",
                    "arguments": json.dumps({"title": "Test operation", "code": code}),
                },
            }
        ],
    }


def answer(text):
    return {"role": "assistant", "content": text}


class ModelServer:
    def __init__(self, replies):
        self.replies = deque(replies)
        self.requests = []
        owner = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                return

            def do_post(self):
                request = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                owner.requests.append(request)
                # Compressors make independent requests without desktop tools.
                reply = (
                    owner.replies.popleft()
                    if request.get("tools")
                    else answer("Task memory: retain counter and user intent.")
                )
                finish = "tool_calls" if reply.get("tool_calls") else "stop"
                self.send_response(200)
                if request.get("stream"):
                    self.send_header("Content-Type", "text/event-stream")
                    self.end_headers()
                    delta = {**reply}
                    if "tool_calls" in delta:
                        delta["tool_calls"] = [
                            {"index": index, **call} for index, call in enumerate(delta["tool_calls"])
                        ]
                    chunks = [
                        {
                            "id": "completion-test",
                            "model": "test-model",
                            "choices": [{"index": 0, "delta": delta, "finish_reason": None}],
                        },
                        {
                            "id": "completion-test",
                            "model": "test-model",
                            "choices": [{"index": 0, "delta": {}, "finish_reason": finish}],
                        },
                    ]
                    for chunk in chunks:
                        self.wfile.write(("data: " + json.dumps(chunk) + "\n\n").encode())
                    self.wfile.write(b"data: [DONE]\n\n")
                else:
                    self.send_header("Content-Type", "application/json")
                    self.end_headers()
                    self.wfile.write(
                        json.dumps(
                            {
                                "id": "completion-test",
                                "model": "test-model",
                                "object": "chat.completion",
                                "choices": [{"index": 0, "message": reply, "finish_reason": finish}],
                                "usage": {"prompt_tokens": 100, "completion_tokens": 10, "total_tokens": 110},
                            }
                        ).encode()
                    )

            # BaseHTTPRequestHandler dispatches requests through this protocol name.
            do_POST = do_post

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def model(self):
        from openjiuwen.core.foundation.llm import Model, ModelClientConfig, ModelRequestConfig

        return Model(
            model_client_config=ModelClientConfig(
                client_provider="OpenAI",
                api_key="test-only",
                api_base=f"http://127.0.0.1:{self.server.server_port}/v1",
            ),
            model_config=ModelRequestConfig(model="test-model"),
        )

    def close(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=3)


@pytest.fixture
def model_server():
    servers = []

    def create(replies):
        server = ModelServer(replies)
        servers.append(server)
        return server

    yield create
    for server in servers:
        server.close()
