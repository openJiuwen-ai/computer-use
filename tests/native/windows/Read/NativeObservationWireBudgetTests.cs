// Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Text;

namespace ComputerUse.Windows
{
    internal static class NativeObservationWireBudgetTests
    {
        static void Require(bool condition, string message)
        {
            if (!condition)
                throw new InvalidOperationException(message);
        }

        static Dictionary<string, object> Observe(int count)
        {
            var rows = new List<object>();
            var edges = new List<object>();
            var fields = new[] { "Name" }.Concat(Enumerable.Range(0, 72).Select(i => "Provider.Field" + i)).ToArray();
            for (int index = 0; index < count; index++)
            {
                string id = "node:" + index;
                var statuses = fields.Skip(1).ToDictionary(field => field, field => (object)Wire.Obj(
                    "state", "timeout", "detail", "Provider transaction reached its deadline",
                    "nativeError", Wire.Obj("type", "COMException", "hresult", unchecked((int)0x8001011f),
                                            "message", new String('x', 420))));
                rows.Add(Wire.Obj("node", id, "requestedFields", fields, "values", Wire.Obj("Name", "Visible " + id),
                                  "fieldStatus", statuses, "actions", Wire.Obj("state", "complete", "items", new object[0])));
                edges.Add(Wire.Obj("source", id, "property", "RawViewChildren", "targets",
                                   index + 1 < count ? new[] { "node:" + (index + 1) } : new string[0],
                                   "state", "complete", "certainty", "confirmed"));
            }
            return NativeObservationGraph.Encode("session", "surface", Wire.Stamp(Wire.Now), new[] { "node:0" },
                                                 rows, edges, new string[0], new string[0], new[] { "RawViewChildren" });
        }

        static void TraversalCoverage(bool expandChild)
        {
            var notes = new List<string>();
            var traversal = new NativeObservationTraversal(2000, 16);
            Require(traversal.MaxNodes == 1024, "the relation count must still bound the collection ceiling");
            var rows = new List<object>();
            var edges = new List<object>();
            var seen = new HashSet<string>();
            Action<string, string, string[]> edge = (id, property, targets) => edges.Add(Wire.Obj(
                "source", id, "property", property, "targets", targets, "state", "complete", "certainty", "confirmed"));
            Action<string, string[], string[]> node = (id, children, parent) =>
            {
                seen.Add(id);
                rows.Add(Wire.Obj("node", id, "requestedFields", new[] { "Name" },
                                  "values", Wire.Obj("Name", id), "fieldStatus", Wire.Obj(),
                                  "actions", Wire.Obj("state", "complete", "items", new object[0])));
                edge(id, "RawViewChildren", children);
                edge(id, "RawViewParent", parent);
            };
            node("root", new[] { "child" }, new[] { "outside" });
            traversal.Reference("outside", false);
            traversal.Reference("child", true);
            if (expandChild)
                node("child", new string[0], new[] { "root" });
            var unread = traversal.Unread(seen, notes);
            var graph = NativeObservationGraph.Encode("session", "surface", Wire.Stamp(Wire.Now), new[] { "root" },
                rows, edges, unread, notes, new[] { "RawViewChildren", "RawViewParent" });
            var read = Wire.Object(graph["read"]);
            var declared = Wire.Array(graph["nodes"]).Select(Wire.Object).ToDictionary(row => (string)row["id"]);
            Require(declared["outside"]["properties"] == null,
                    "the exact out-of-scope parent must remain declared without an invented body");
            Require(Equals(read["complete"], expandChild) && Wire.Array(read["issues"]).Length == (expandChild ? 0 : 1),
                    "only omitted requested traversal may mark this tiny graph partial");
            Require(unread.SequenceEqual(expandChild ? new string[0] : new[] { "child" }) &&
                    (declared["child"]["properties"] != null) == expandChild,
                    "requested omissions must remain declared and distinguishable from external targets");
        }

        static void TraversalNodeLimit()
        {
            var notes = new List<string>();
            var traversal = new NativeObservationTraversal(2000, 16);
            traversal.Reference("remaining", true);
            var seen = new HashSet<string>(Enumerable.Range(0, traversal.MaxNodes).Select(i => "node:" + i));
            Require(traversal.Unread(seen, notes).SequenceEqual(new[] { "remaining" }) && notes.Any(note =>
                        note.Contains("relation-status node limit")),
                    "hitting the actual lowered ceiling with queued work must report the node limit");
        }

        static void StrictWindowBodies()
        {
            var outside = new NativeNodeRecord(new NativeObservationScope(NativeOwnershipState.Outside, "other HWND"),
                                               null, false);
            var virtualChild = new NativeNodeRecord(NativeObservationScope.DeferredRelation, null, false);
            Require(!NativeObservationTraversal.IncludesBody(outside, true),
                    "a strict window must never read a confirmed outside node body");
            Require(NativeObservationTraversal.IncludesBody(outside, false),
                    "an ordinary observation retains its diagnostic outside-node behavior");
            Require(NativeObservationTraversal.IncludesBody(virtualChild, true),
                    "an HWND-less virtual descendant is not evidence of another window");
            var root = Wire.Obj("node", "root", "requestedFields", new[] { "Name" },
                "values", Wire.Obj("Name", "Selected"), "fieldStatus", Wire.Obj(),
                "actions", Wire.Obj("state", "complete", "items", new object[0]));
            var relation = Wire.Obj("source", "root", "property", "RawViewChildren", "targets", new[] { "other" },
                                    "state", "complete", "certainty", "confirmed");
            var graph = NativeObservationGraph.Encode("session", "surface", Wire.Stamp(Wire.Now), new[] { "root" },
                new[] { root }, new[] { relation }, new string[0], new string[0], new[] { "RawViewChildren" },
                outside: new[] { "other" });
            var declaration = Wire.Array(graph["nodes"]).Select(Wire.Object).Single(row => Equals(row["id"], "other"));
            Require(declaration["properties"] == null && Wire.Array(Wire.Object(declaration["read"])["issues"]).Any(
                        issue => Equals(Wire.Object(issue)["reason"], "outside_window_scope")),
                    "retain a typed outside-window declaration without its properties");
            var original = Wire.Array(graph["nodes"]).Select(Wire.Object).Single(row => Equals(row["id"], "root"));
            var retained = Wire.Array(original["properties"]).Select(Wire.Object).Single(property =>
                Equals(Wire.Array(property["key"])[1], "RawViewChildren"));
            Require(Equals(Wire.Object(retained["read"])["complete"], true),
                    "outside-node exclusion must not invent a gap in the completed relation");
        }

        static void StrictWindowRootCandidates()
        {
            var peer = new NativeWindowRecord { Native = new Window { Ref = "blocking-provider" } };
            var selected = new NativeWindowRecord { Native = new Window { Ref = "selected" } };
            var catalogue = new[] { peer, selected };
            Require(NativeRootProbe.Candidates(catalogue, "selected").SequenceEqual(new[] { selected }),
                    "a strict window read must not start the sibling root provider");
            Require(NativeRootProbe.Candidates(catalogue, null).SequenceEqual(catalogue),
                    "the ordinary root probe must retain full catalogue behavior");
        }

        static int Main(string[] args)
        {
            try
            {
                TraversalCoverage(true);
                TraversalCoverage(false);
                TraversalNodeLimit();
                StrictWindowBodies();
                StrictWindowRootCandidates();
                var small = Observe(2);
                Require(Equals(Wire.Object(small["read"])["complete"], true), "small reads must stay complete");
                var graph = Observe(150);
                int bytes = Encoding.UTF8.GetByteCount(Wire.Json(graph));
                Require(bytes <= 4 * 1024 * 1024, "typed observation exceeded the response budget: " + bytes);
                var read = Wire.Object(graph["read"]);
                Require(Equals(read["complete"], false) && Wire.Array(read["issues"]).Any(
                            raw => Equals(Wire.Object(raw)["reason"], "observation_wire_budget_exhausted")),
                        "the delivered read must account for omitted native facts");
                var nodes = Wire.Array(graph["nodes"]).Select(Wire.Object).ToDictionary(row => (string)row["id"]);
                Require(nodes["node:0"]["properties"] != null && nodes.Count < 150,
                        "the root's measured properties must survive the cutoff");
                var seen = nodes.Values.Where(row => row["properties"] != null).ToArray();
                foreach (var row in seen)
                {
                    var relation = Wire.Array(row["properties"]).Select(Wire.Object).Single(property =>
                        Equals(Wire.Array(property["key"])[1], "RawViewChildren"));
                    var items = Wire.Array(Wire.Object(Wire.Object(relation["value"])["data"])["items"]);
                    Require(items.All(item => nodes.ContainsKey((string)Wire.Object(Wire.Object(item)["data"])["ref"])),
                            "all retained relationship targets must be declared");
                }
                if (args.Length == 1)
                    File.WriteAllText(args[0], Wire.Json(graph));
                Console.WriteLine("Windows typed observation wire budget passed: " + bytes + " bytes");
                return 0;
            }
            catch (Exception error)
            {
                Console.Error.WriteLine(error.GetType().Name + ": " + error.Message);
                return 1;
            }
        }
    }
}
