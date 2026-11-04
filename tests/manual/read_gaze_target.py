"""Read one already-confirmed gaze target; save local evidence without a model."""

import argparse
import asyncio
import base64
from pathlib import Path

from computer_use_agent import AgentConfig, ComputerUseToolkit


async def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("connection", type=Path)
    parser.add_argument("output", type=Path, help="New directory for the observation and screenshots")
    parser.add_argument("--expected-selection-id", help="Required for a voice selection")
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=False)
    config = AgentConfig(desktop="shared", gaze_connection_file=str(args.connection))
    async with ComputerUseToolkit(config) as toolkit:
        inputs = {"expected_selection_id": args.expected_selection_id} if args.expected_selection_id else {}
        result = await toolkit.gaze_tool.invoke(inputs)
        if not result.success:
            raise RuntimeError(result.error)
        (args.output / "observation.txt").write_text(result.data["content"], encoding="utf-8")
        for index, block in enumerate(result.data["mcp_result"]["content"]):
            if block["type"] == "image":
                suffix = ".png" if block["mimeType"] == "image/png" else ".jpg"
                (args.output / f"capture-{index}{suffix}").write_bytes(base64.b64decode(block["data"], validate=True))


if __name__ == "__main__":
    asyncio.run(main())
