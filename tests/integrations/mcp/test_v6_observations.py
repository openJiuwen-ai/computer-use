import sys
import tempfile
import unittest
from pathlib import Path

from repository import ROOT

from mcp_client_fixture import MCPClient

"V6 read modes retain separate host diff baselines through real stdio MCP."


class V6ObservationTests(unittest.IsolatedAsyncioTestCase):
    async def test_sectioned_menus_multiwindow_and_paged_reads_use_structured_diffs(self):
        for mode in ("observation-sdk-menu", "observation-sdk-owned-modal", "observation-sdk-pages"):
            with self.subTest(mode=mode), tempfile.TemporaryDirectory(prefix="v6-diff-") as folder:
                client = MCPClient(capabilities={"experimental": {"computer-use-history": {"version": 1}}})
                await client.connect(dict(command=sys.executable, args=["-m", "computer_use_mcp"]))
                previous = None

                async def call(code, *, client=client):
                    nonlocal previous
                    result = await client.callTool(
                        dict(
                            name="computer_use",
                            arguments={"code": code},
                            _meta={"computer-use-history": {"version": 1, "previous": previous}},
                        )
                    )
                    previous = result["_meta"]["computer-use-history"]["cursor"]
                    self.assertFalse(result.get("isError"), result)
                    return "\n".join(part["text"] for part in result["content"] if part["type"] == "text")

                try:
                    setup = (
                        f"""import sys
from computer_use.native.connection import """
                        f"""Connection
from """
                        f"""computer_use.client.factory import nativeComputer
from """
                        f"""computer_use.native.contracts """
                        f"""import HostPolicy
from computer_use.native.transport import """
                        f"""ConnectionPort
conn=Connection(lambda:None,{{"command":sys.executable,"args":["""
                        f"""{str(ROOT / "tests/fixtures/v5-driver.py")!r}"""
                        f""","""
                        f"""{mode!r}"""
                        f""","""
                        f"""{str(Path(folder) / "wire.jsonl")!r}"""
                        f"""]}},protocol_version=6)
computer=nativeComputer(ConnectionPort(conn,HostPolicy()))
app=computer.app("Native """
                        f"""fixture")
state=await app.read()"""
                    )
                    full = await call(setup)
                    self.assertIn("Tree: full", full)
                    unchanged = await call("await app.read()")
                    if mode == "observation-sdk-pages":
                        # Two visible children plus mandatory partial/continuation context
                        # are shorter as a full view than as an unchanged summary.
                        self.assertIn("Tree: full", unchanged)
                        self.assertIn("Children: 2/300 read; partial", unchanged)
                        self.assertIn('cursor="p1"', unchanged)
                        self.assertNotIn("Removed element IDs:", unchanged)
                    else:
                        self.assertIn("Tree: unchanged", unchanged)
                        self.assertIn("read(full=True)", unchanged)
                    if mode == "observation-sdk-menu":
                        self.assertIn("Application menu (context of this window):", unchanged)
                        self.assertIn("Additional native facts/actions/relations:", unchanged)
                        changed = await call(
                            ('field=await app.element(1)\nawait field.typeText("new",replace=True)\nawait app.read()')
                        )
                        self.assertIn("Tree: diff", changed)
                        self.assertIn("Value: new", changed)
                    if mode == "observation-sdk-owned-modal":
                        self.assertIn("Windows:", unchanged)
                        self.assertIn("Window 0:", unchanged)
                        self.assertIn("Window 1:", unchanged)
                    # Explicit full output and a lost history checkpoint reset selection.
                    self.assertIn("Tree: full", await call("await app.read(full=True)"))
                    previous = None
                    self.assertIn("Tree: full", await call("await app.read()"))
                    await call("conn.close()\nawait conn.waitClosed()")
                finally:
                    await client.close()

    async def test_full_diff_and_image_views_do_not_erase_each_others_observations(self):
        client = MCPClient(capabilities={"experimental": {"computer-use-history": {"version": 1}}})
        await client.connect(dict(command=sys.executable, args=["-m", "computer_use_mcp"]))
        previous = None

        async def call(code):
            nonlocal previous
            result = await client.callTool(
                dict(
                    name="computer_use",
                    arguments={"code": code},
                    _meta={"computer-use-history": {"version": 1, "previous": previous}},
                )
            )
            previous = result.get("_meta", {}).get("computer-use-history", {}).get("cursor")
            self.assertFalse(result.get("isError"), result)
            return result, "\n".join(b["text"] for b in result["content"] if b["type"] == "text")

        try:
            with tempfile.TemporaryDirectory(prefix="v6-mcp-") as folder:
                setup = (
                    f"""import sys
from computer_use.native.connection import """
                    f"""Connection
from """
                    f"""computer_use.client.factory import nativeComputer
from """
                    f"""computer_use.native.contracts """
                    f"""import HostPolicy
from computer_use.native.transport import """
                    f"""ConnectionPort
conn=Connection(lambda:None,{{"command":sys.executable,"args":["""
                    f"""{str(ROOT / "tests/fixtures/v5-driver.py")!r}"""
                    f""","observation-sdk","""
                    f"""{str(Path(folder) / "wire.jsonl")!r}"""
                    f"""]}},protocol_version=6)
computer=nativeComputer(ConnectionPort(conn,HostPolicy()))
app=computer.app("Native """
                    f"""fixture")
state=await app.read()"""
                )
                first, text = await call(setup)
                self.assertIn("Tree: full", text)
                self.assertFalse(any(b["type"] == "image" for b in first["content"]))
                _, text = await call(
                    ('field=await app.element(1)\nawait field.typeText("new",replace=True)\nawait app.read()')
                )
                self.assertTrue("Tree: diff" in text or "Tree: full" in text)
                _, text = await call("await app.read()")
                self.assertIn("Tree: unchanged", text)
                self.assertIn("read(full=True)", text)
                _, text = await call("await app.read(full=True)")
                self.assertIn("Tree: full", text)
                picture, text = await call('await app.read(view="image")')
                self.assertTrue(any(b["type"] == "image" for b in picture["content"]))
                self.assertNotIn("Removed element", text)
                _, text = await call("await app.read()")
                self.assertNotIn("Removed element", text)
                # A retained full body is still complete even when the host
                # chose a diff; explicit print is not needed for ordinary use.
                _, text = await call("print(state.text)")
                self.assertIn("Tree: full", text)
                await call("conn.close()\nawait conn.waitClosed()")
        finally:
            await client.close()
