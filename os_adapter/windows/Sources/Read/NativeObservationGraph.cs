// Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

using System;
using System.Collections;
using System.Collections.Generic;
using System.Linq;
using System.Text;

namespace ComputerUse.Windows
{
    // The wire graph owns no UIA objects. Native bindings stay in their
    // existing store; this encoder preserves collection records and edges.
    internal sealed class NativeObservationReference
    {
        internal readonly string Id;
        internal NativeObservationReference(string id)
        {
            Id = id;
        }
    }

    internal sealed class NativeObservationValue
    {
        internal readonly string Type;
        internal readonly object Data;
        internal readonly object[] Metadata;
        internal string Api;
        internal NativeObservationValue(string type, object data, params object[][] metadata)
        {
            Type = type;
            Data = data;
            Metadata = metadata;
        }
    }

    internal sealed class NativeObservationGap
    {
        internal readonly string Reason, Detail;
        internal NativeObservationGap(string reason, string detail)
        {
            Reason = reason;
            Detail = detail;
        }
    }

    internal sealed class NativeObservationTraversal
    {
        readonly HashSet<string> required = new HashSet<string>();
        readonly bool capped;
        internal readonly int MaxNodes;

        internal NativeObservationTraversal(int maximum, int relations)
        {
            MaxNodes = Math.Min(maximum, 16384 / relations);
            capped = MaxNodes < maximum;
        }

        internal void Reference(string id, bool expand)
        {
            if (expand)
                required.Add(id);
        }

        internal static bool IncludesBody(NativeNodeRecord node, bool strictWindow)
        {
            return !strictWindow || !node.Outside;
        }

        internal string[] Unread(HashSet<string> seen, List<string> notes, HashSet<string> outside = null)
        {
            // A complete edge can name a declaration beyond the requested traversal.
            // The graph keeps all such edge targets without claiming an omitted body.
            var unread = required.Where(id => !seen.Contains(id) && (outside == null || !outside.Contains(id))).ToArray();
            if (unread.Length != 0)
            {
                if (capped && seen.Count >= MaxNodes)
                    NativeReadStore.Note(notes, "The requested traversal reached its relation-status node limit.");
                NativeReadStore.Note(notes, "Some requested related nodes remain unread.");
            }
            return unread;
        }
    }

    internal static class NativeObservationGraph
    {
        // A cap on the actual typed graph, before it is encoded by both
        // worker and supervisor. The collector's raw-row budget is separate.
        const int WireBudget = 4 * 1024 * 1024;

        internal static object[] Pair(string key, object value)
        {
            return new object[] { key, Value(value) };
        }

        internal static Dictionary<string, object> Value(object value, string type = null, object[] metadata = null,
                                                         Func<object, NativeObservationReference> opaque = null)
        {
            var annotated = value as NativeObservationValue;
            if (annotated != null)
                return Value(annotated.Data, annotated.Type, annotated.Metadata, opaque);
            object data;
            var reference = value as NativeObservationReference;
            var gap = value as NativeObservationGap;
            var record = value as IDictionary<string, object>;
            var bytes = value as byte[];
            var text = value as string;
            bool unpaired = false;
            if (text != null)
                for (int i = 0; i < text.Length; i++)
                {
                    if (Char.IsHighSurrogate(text[i]))
                    {
                        if (i + 1 < text.Length && Char.IsLowSurrogate(text[i + 1]))
                            i++;
                        else
                        {
                            unpaired = true;
                            break;
                        }
                    }
                    else if (Char.IsLowSurrogate(text[i]))
                    {
                        unpaired = true;
                        break;
                    }
                }

            if (reference != null)
            {
                data = Wire.Obj("ref", reference.Id);
                type = type ?? "IUIAutomationElement";
            }
            else if (gap != null)
                data = Wire.Obj("fields",
                                new[] { new object[] { null, Wire.Obj("issue", Issue(gap.Reason, gap.Detail)) } });
            else if (bytes != null)
                data = Wire.Obj("bytes", Convert.ToBase64String(bytes));
            else if (unpaired)
            {
                // Framework JSON can write a raw surrogate that stdout's UTF-8
                // encoder replaces. Preserve the original UTF-16 code units.
                var units = new byte[text.Length * 2];
                for (int i = 0; i < text.Length; i++)
                {
                    units[i * 2] = (byte)text[i];
                    units[i * 2 + 1] = (byte)(text[i] >> 8);
                }

                data = Wire.Obj("bytes", Convert.ToBase64String(units));
                metadata =
                    (metadata ?? new object[0]).Concat(new object[] { Pair("string_encoding", "utf-16le") }).ToArray();
            }
            else if (value is decimal)
            {
                int[] bits = Decimal.GetBits((decimal)value);
                data = Wire.Obj("fields", new[] { "low", "middle", "high", "flags" }
                                              .Select((name, index) => new object[] { name, Value(bits[index]) })
                                              .ToArray());
            }
            else if (record != null && record.Count == 2 && record.ContainsKey("nativeType") &&
                     Equals(record["nativeType"], "UIA.Double") && record.ContainsKey("value") &&
                     new[] { "NaN", "+Infinity", "-Infinity" }.Contains(record["value"] as string))
            {
                string name = Convert.ToString(record["value"]);
                data = Wire.Obj("float", name == "NaN" ? "nan" : name == "+Infinity" ? "inf" : "-inf");
                type = type ?? "UIA.Double";
            }
            else if (record != null)
                data = Wire.Obj(
                    "fields",
                    record.Select(item => new object[] { item.Key, Value(item.Value, opaque: opaque) }).ToArray());
            else if (value is double && (Double.IsNaN((double)value) || Double.IsInfinity((double)value)))
                data = Wire.Obj("float", Double.IsNaN((double)value) ? "nan" : (double)value > 0 ? "inf" : "-inf");
            else if (value is double && (double)value == 0 && BitConverter.DoubleToInt64Bits((double)value) < 0)
                data = Wire.Obj("float", "-0");
            else if (value is IEnumerable && !(value is string))
                data = Wire.Obj(
                    "items", ((IEnumerable)value).Cast<object>().Select(item => Value(item, opaque: opaque)).ToArray());
            else if (value == null || value is string || value is bool || value is byte || value is sbyte ||
                     value is short || value is ushort || value is int || value is uint || value is long ||
                     value is ulong || value is float || value is double)
                data = value;
            else
            {
                if (opaque == null)
                    throw new Failure("observation_incomplete",
                                      "a native result requires an opaque resource binding: " +
                                          value.GetType().FullName);
                var retained = opaque(value);
                if (retained != null)
                    return Value(retained, value.GetType().FullName);
                data = Wire.Obj(
                    "fields", new[] { new object[] {
                        null,
                        Wire.Obj("issue",
                                 Issue("native_resource_budget_exhausted",
                                       "The acquired " + value.GetType().FullName +
                                           " reference could not be retained within this operation's resource budget."))
                    } });
            }

            var result = Wire.Obj("nativeType", type ?? (value == null ? "VARIANT.NULL" : value.GetType().FullName),
                                  "data", data);
            if (metadata != null && metadata.Length != 0)
                result["metadata"] = metadata;
            return result;
        }

        static bool HasGap(object value)
        {
            var record = value as IDictionary<string, object>;
            if (record != null)
                return record.ContainsKey("issue") || record.Values.Any(HasGap);
            return value is IEnumerable && !(value is string) && ((IEnumerable)value).Cast<object>().Any(HasGap);
        }

        internal static Dictionary<string, object> Issue(string reason, string detail = null, object status = null,
                                                         object continuation = null,
                                                         Func<object, NativeObservationReference> opaque = null)
        {
            var result = Wire.Obj("reason", reason);
            if (detail != null)
                result["detail"] = detail;
            if (status != null)
                result["nativeStatus"] = Value(status, opaque: opaque);
            if (continuation != null)
                result["continuation"] = Value(continuation, opaque: opaque);
            return result;
        }

        internal static Dictionary<string, object> Read(string api, bool? complete, IEnumerable<object> details = null,
                                                        IEnumerable<object> issues = null)
        {
            return Wire.Obj("api", api, "complete", complete, "details",
                            details == null ? new object[0] : details.ToArray(), "issues",
                            issues == null ? new object[0] : issues.ToArray());
        }

        static string State(string state)
        {
            if (state == "notProbed")
                return "not_read";
            if (state == "truncated" || state == "partial")
                return "error";
            return new[] { "value", "absent", "unsupported", "not_read", "error", "timeout", "unavailable", "redacted" }
                           .Contains(state)
                       ? state
                       : "error";
        }

        static object StatusEvidence(Dictionary<string, object> status, string state, string detail)
        {
            if (status == null)
                return null;
            var distinct = new Dictionary<string, object>(status);
            object value;
            if (distinct.TryGetValue("state", out value) && Equals(value, state))
                distinct.Remove("state");
            if (distinct.TryGetValue("detail", out value) && Equals(value, detail))
                distinct.Remove("detail");
            // Ordinary pattern absence is an explicit state, not a failed
            // native transaction. Its managed sentinel's HRESULT is synthetic.
            if ((state == "unsupported" || state == "absent") &&
                distinct.TryGetValue("nativeError", out value))
            {
                var error = value as Dictionary<string, object>;
                object type;
                if (error != null && error.TryGetValue("type", out type) &&
                    Equals(type, typeof(NativeFieldUnavailable).FullName))
                    distinct.Remove("nativeError");
            }
            return distinct.Count == 0 ? null : distinct;
        }

        internal static Dictionary<string, object> Property(string name, object payload, bool hasPayload,
                                                            object status = null, string space = "windows.uia",
                                                            Func<object, NativeObservationReference> opaque = null)
        {
            var nativeStatus = status as Dictionary<string, object>;
            var annotated = payload as NativeObservationValue;
            string api = annotated == null || annotated.Api == null ? "IUIAutomation" : annotated.Api;
            if (nativeStatus != null && nativeStatus.ContainsKey("nativeReadSource"))
                api = (string)nativeStatus["nativeReadSource"];
            string state =
                nativeStatus != null && nativeStatus.ContainsKey("state") ? (string)nativeStatus["state"] : "value";
            var result = Wire.Obj("key", new object[] { space, name }, "value",
                                  hasPayload ? Value(payload, opaque: opaque) : null, "state",
                                  hasPayload ? "value" : State(state));
            if (nativeStatus != null)
            {
                object continuation = null;
                if (nativeStatus.ContainsKey("textRef"))
                    continuation = nativeStatus;
                string detail = nativeStatus.ContainsKey("detail") ? nativeStatus["detail"] as string : null;
                result["read"] =
                    Read(api, false, null, new[] {
                        Issue(state, detail, StatusEvidence(nativeStatus, hasPayload ? "truncated" : State(state), detail),
                              continuation, opaque)
                    });
            }
            else if (annotated != null && annotated.Api != null)
                result["read"] = Read(api, hasPayload);
            if (hasPayload && HasGap(result["value"]) && nativeStatus == null)
                result["read"] = Read(api, false, null, new[] { Issue("native_resource_budget_exhausted") });
            return result;
        }

        static Dictionary<string, object> Relation(Dictionary<string, object> edge,
                                                   Func<object, NativeObservationReference> opaque)
        {
            string state = (string)edge["state"];
            bool payload = state == "complete" || state == "partial" || state == "absent";
            var result = Property(
                (string)edge["property"],
                Wire.Array(edge["targets"]).Cast<string>().Select(id => new NativeObservationReference(id)).ToArray(),
                payload,
                payload ? null : Wire.Obj("state", state, "detail", edge.ContainsKey("detail") ? edge["detail"] : null),
                opaque: opaque);
            var details = new List<object> { Pair("relation", true) };
            foreach (string key in new[] { "certainty", "state", "offset", "total", "cursor", "detail",
                                           "membershipObservedAt", "memberIssues" }

            )
                if (edge.ContainsKey(key))
                    details.Add(new object[] { key, Value(edge[key], opaque: opaque) });
            result["read"] =
                Read("IUIAutomation.RawView", state == "complete" || state == "absent", details,
                     state == "complete" || state == "absent"
                         ? null
                         : new[] { Issue(state, edge.ContainsKey("detail") ? edge["detail"] as string : null,
                                         edge.ContainsKey("nativeStatus") ? StatusEvidence(
                                             edge["nativeStatus"] as Dictionary<string, object>, state,
                                             edge.ContainsKey("detail") ? edge["detail"] as string : null) : null,
                                         edge.ContainsKey("cursor") ? edge : null, opaque) });
            return result;
        }

        static Dictionary<string, object> Node(string id, object[] properties = null)
        {
            return Wire.Obj("id", id, "source", "windows.uia", "kind", "element", "nativeType", "IUIAutomationElement",
                            "properties", properties);
        }

        static void References(object data, Action<string> declare)
        {
            var record = data as IDictionary<string, object>;
            if (record != null)
            {
                object id;
                if (record.TryGetValue("ref", out id))
                    declare((string)id);
                foreach (object value in record.Values)
                    References(value, declare);
            }
            else if (data is IEnumerable && !(data is string))
                foreach (object item in (IEnumerable)data)
                    References(item, declare);
        }

        static int WireBytes(object value)
        {
            return Encoding.UTF8.GetByteCount(Wire.Json(value));
        }

        // A complete node and its reference declarations are admitted as a
        // unit. Unexpanded referenced elements retain identity, never a false
        // empty body. No admitted field, action or relation is cut mid-value.
        sealed class BoundedNodes
        {
            readonly Dictionary<string, Dictionary<string, object>> source;
            readonly Dictionary<string, Dictionary<string, object>> kept =
                new Dictionary<string, Dictionary<string, object>>();
            int bytes;

            internal BoundedNodes(Dictionary<string, Dictionary<string, object>> source, int bytes)
            {
                this.source = source;
                this.bytes = bytes;
            }

            internal object[] Nodes { get { return kept.Values.ToArray(); } }

            internal Dictionary<string, object> Declaration(string id)
            {
                var row = source[id];
                if (!Equals(row["kind"], "element") || row["properties"] == null)
                    return row;
                var stub = new Dictionary<string, object>(row);
                stub["properties"] = null;
                stub.Remove("read");
                return stub;
            }

            internal bool Admit(object value, Dictionary<string, object> row = null, int limit = WireBudget)
            {
                var staged = new Dictionary<string, Dictionary<string, object>>();
                var pending = new Queue<Dictionary<string, object>>();
                if (row != null)
                    staged.Add((string)row["id"], row);
                Action<string> dependency = id =>
                {
                    if (kept.ContainsKey(id) || staged.ContainsKey(id))
                        return;
                    var stub = Declaration(id);
                    staged.Add(id, stub);
                    pending.Enqueue(stub);
                };
                References(value, dependency);
                while (pending.Count != 0)
                    References(pending.Dequeue(), dependency);

                int added = row == null ? WireBytes(value) + 1 : 0;
                foreach (var entry in staged)
                {
                    Dictionary<string, object> prior;
                    added += WireBytes(entry.Value) + 1;
                    if (kept.TryGetValue(entry.Key, out prior))
                        added -= WireBytes(prior) + 1;
                    if (bytes + added > limit)
                        return false;
                }
                if (bytes + added > limit)
                    return false;
                foreach (var entry in staged)
                    kept[entry.Key] = entry.Value;
                bytes += added;
                return true;
            }
        }

        static Dictionary<string, object> Bound(
            Dictionary<string, object> graph, Dictionary<string, Dictionary<string, object>> declarations,
            IEnumerable<string> roots, IEnumerable<string> ordered, Dictionary<string, object> read)
        {
            var issues = Wire.Array(read["issues"]);
            read["issues"] = new object[0];
            var cutoff = Issue("observation_wire_budget_exhausted",
                               "The delivered native graph reached its byte budget. Some captured facts were omitted.");
            var budget = new BoundedNodes(declarations, WireBytes(graph) + WireBytes(cutoff) + 4);
            foreach (string root in roots)
            {
                var stub = budget.Declaration(root);
                if (!budget.Admit(stub, stub))
                    throw new Failure("observation_unavailable", "root declaration exceeds the observation byte budget");
            }
            var includedIssues = new List<object>();
            bool truncated = false;
            foreach (object issue in issues)
            {
                if (!budget.Admit(issue, limit: WireBudget / 8))
                {
                    truncated = true;
                    break;
                }
                includedIssues.Add(issue);
            }
            bool omittedNodes = false;
            foreach (string id in ordered.Distinct())
            {
                var row = declarations[id];
                if (!budget.Admit(row, row))
                {
                    omittedNodes = true;
                    break;
                }
            }
            if (truncated || omittedNodes)
            {
                includedIssues.Add(cutoff);
                read["complete"] = false;
            }
            read["issues"] = includedIssues.ToArray();
            graph["nodes"] = budget.Nodes;
            return graph;
        }

        internal static Dictionary<string, object> Encode(
            string session, string surface, Dictionary<string, object> observed, IEnumerable<string> roots,
            IEnumerable<object> records, IEnumerable<object> edges, IEnumerable<string> unread,
            IEnumerable<string> notes, IEnumerable<string> requestedRelations, string changeToken = null,
            IDictionary<string, Dictionary<string, object>> nativeValues = null,
            IEnumerable<Dictionary<string, object>> resources = null, Func<string, object> identity = null,
            Func<object, NativeObservationReference> opaque = null, IEnumerable<object> cachedFailures = null,
            IEnumerable<string> outside = null)
        {
            var declarations = new Dictionary<string, Dictionary<string, object>>();
            Action<string> declare = id =>
            {
                if (declarations.ContainsKey(id))
                    return;
                var row = Node(id);
                object nativeIdentity = identity == null ? null : identity(id);
                if (nativeIdentity != null)
                    row["identity"] = Value(nativeIdentity, opaque: opaque);
                declarations.Add(id, row);
            };
            foreach (string root in roots)
                declare(root);
            foreach (string id in unread)
                declare(id);
            if (outside != null)
                foreach (string id in outside)
                {
                    declare(id);
                    declarations[id]["read"] = Read("IUIAutomation.RawView", false, null,
                        new[] { Issue("outside_window_scope", "The native object belongs to another window.") });
                }
            foreach (object raw in records)
            {
                var row = Wire.Object(raw);
                string id = (string)row["node"];
                declare(id);
                var values = Wire.Object(row["values"]);
                var statuses = Wire.Object(row["fieldStatus"]);
                var fields = Wire.Array(row["requestedFields"]).Cast<string>().ToArray();
                var properties = new List<object>();
                foreach (string field in fields.Concat(values.Keys).Concat(statuses.Keys).Distinct())
                {
                    object payload = null, status = null;
                    bool has = values.TryGetValue(field, out payload);
                    statuses.TryGetValue(field, out status);
                    Dictionary<string, object> exact;
                    if (has && nativeValues != null && nativeValues.TryGetValue(id, out exact) &&
                        exact.ContainsKey(field))
                        payload = exact[field];
                    properties.Add(Property(field, payload, has, status, opaque: opaque));
                }

                var actions = Wire.Object(row["actions"]);
                var catalogue = Property("actions", actions["items"], true, null, "toolkit", opaque);
                catalogue["read"] = Read(
                    "IUIAutomation.GetSupportedPatterns", Equals(actions["state"], "complete"),
                    actions.Where(p => p.Key != "items" && p.Key != "nativeStatus").Select(p => Pair(p.Key, p.Value)),
                    Equals(actions["state"], "complete")
                        ? null
                        : new[] { Issue(Convert.ToString(actions["state"]),
                                        actions.ContainsKey("detail") ? actions["detail"] as string : null, actions,
                                        opaque: opaque) });
                properties.Add(catalogue);
                declarations[id]["properties"] = properties;
                declarations[id]["read"] =
                    Read("IUIAutomation.RawView", statuses.Count == 0, new[] { Pair("requested_fields", fields) });
            }

            foreach (object raw in edges)
            {
                var edge = Wire.Object(raw);
                string source = (string)edge["source"];
                declare(source);
                foreach (string target in Wire.Array(edge["targets"]))
                    declare(target);
                var properties = declarations[source]["properties"] as List<object>;
                if (properties == null)
                {
                    properties = new List<object>();
                    declarations[source]["properties"] = properties;
                }

                properties.Add(Relation(edge, opaque));
            }

            var details =
                new List<object> { Pair("surface", surface), Pair("observation_id", observed["id"]),
                                   Pair("requested_relations", requestedRelations.ToArray()), Pair("view", "raw") };
            if (changeToken != null)
                details.Add(Pair("change_token", changeToken));
            var issues = notes.Select(note => (object)Issue(note)).ToList();
            if (cachedFailures != null)
                foreach (var status in cachedFailures)
                    issues.Add(Issue(
                        "cached_property_decode_failed",
                        "A cached property read failed; its original result and the existing current-property fallback are preserved.",
                        status, opaque: opaque));
            var read = Read("IUIAutomation.RawView", issues.Count == 0 && !unread.Any(), details, issues);
            read["intervalNs"] = new[] { (long)(Convert.ToDouble(observed["startedAt"]) * 1000000),
                                         (long)(Convert.ToDouble(observed["completedAt"]) * 1000000) };
            if (resources != null)
                foreach (var resource in resources)
                    declarations.Add((string)resource["id"], resource);
            foreach (var row in declarations.Values.ToArray())
                References(row, declare);
            References(read, declare);
            var rootIds = roots.ToArray();
            var graph = Wire.Obj("kind", "observation", "session", session, "platform", "windows", "roots", rootIds,
                                 "nodes", new object[0], "read", read);
            return Bound(graph, declarations, rootIds, rootIds
                .Concat(records.Select(row => (string)Wire.Object(row)["node"]))
                .Concat(edges.Select(edge => (string)Wire.Object(edge)["source"]))
                .Concat(declarations.Keys), read);
        }
    }
}
