// Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

using System;
using System.Collections.Generic;
using System.Linq;

namespace ComputerUse.Windows
{
    // Build with the driver sources and select this class as the entry point.
    internal static class NativeObservationLineParity
    {
        static void Check(bool condition, string description)
        {
            if (!condition)
                throw new Exception(description);
        }

        static Dictionary<string, object> Row(string id, string title)
        {
            return Wire.Obj(
                "node", id, "requestedFields", new[] { "Name", "Value.Value" },
                "values", Wire.Obj("Name", title, "Value.Value", null),
                "fieldStatus", Wire.Obj("Value.Value", Wire.Obj(
                    "state", "partial", "textRef", "cursor", "totalUnits", 5)),
                "actions", Wire.Obj("state", "complete", "items", new object[0]));
        }

        static Dictionary<string, object> Envelope(NativeObservationGraph.Publication result)
        {
            return Wire.Obj("result", result.Graph, "version", 6, "session", "session", "id", 3,
                            "windowCheckpoint", new object[0], "recycleReadWorker", false);
        }

        static NativeObservationGraph.Publication Publish(params object[] rows)
        {
            var edge = Wire.Obj("source", "root", "property", "children",
                                "targets", new[] { "child", "child", "outside" }, "state", "partial",
                                "certainty", "confirmed", "total", 4, "offset", 0, "cursor", "more");
            return NativeObservationGraph.Encode("session", "surface",
                Wire.Obj("id", "capture", "startedAt", 1, "completedAt", 2),
                new[] { "root" }, rows, new[] { edge }, new[] { "outside" },
                new[] { "partial" }, new[] { "children" }, outside: new[] { "outside" });
        }

        internal static void Main()
        {
            var child = Row("child", "ordinary");
            var root = Row("root", "中文 \"nodes\":null \\ \u2028 \ud83d\ude42");
            var full = Publish(root, child);
            var response = Envelope(full);
            Check(full.Reply(response) == Wire.Json(response), "cached node fragments changed the Windows JSONL");
            // An auxiliary envelope may contain the same member spelling.
            response["pointerCheckpoint"] = Wire.Obj("nodes", null);
            Check(full.Reply(response) == Wire.Json(response), "ambiguous envelope did not use the original writer");

            var capped = Publish(root, Row("child", new string('\u4e2d', 2 * 1024 * 1024)));
            var nodes = Wire.Array(capped.Graph["nodes"]).Select(Wire.Object).ToArray();
            Check(nodes.Select(node => (string)node["id"]).SequenceEqual(new[] { "root", "child", "outside" }),
                  "admitted node and dependency order changed");
            Check(nodes[0]["properties"] != null && nodes[1]["properties"] == null && nodes[2]["properties"] == null,
                  "over-budget replacement must keep the original reference declaration");
            var read = Wire.Object(capped.Graph["read"]);
            var issues = Wire.Array(read["issues"]).Select(Wire.Object);
            Check(Equals(read["complete"], false) &&
                      issues.Any(issue => Equals(issue["reason"], "observation_wire_budget_exhausted")),
                  "the original byte budget must still report incomplete publication");
            response = Envelope(capped);
            Check(capped.Reply(response) == Wire.Json(response), "budget-limited graph changed the Windows JSONL");

            object nested = true;
            for (int i = 0; i < Wire.JsonRecursionLimit; i++)
                nested = new[] { nested };
            var graph = Wire.Obj("kind", "observation", "nodes", new[] { nested });
            var deep = new NativeObservationGraph.Publication(graph, new[] { "null" }, false);
            var deepReply = Envelope(deep);
            Exception original = null, candidate = null;
            try { Wire.Json(deepReply); } catch (Exception error) { original = error; }
            try { deep.Reply(deepReply); } catch (Exception error) { candidate = error; }
            Check(original != null && candidate != null && original.GetType() == candidate.GetType(),
                  "an unsafe fragment must preserve the original recursion failure");
        }
    }
}
