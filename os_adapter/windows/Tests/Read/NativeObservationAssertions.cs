using System;
using System.Collections.Generic;
using System.Linq;

namespace ComputerUse.Windows
{
    // Test-only accessors inspect the graph wire directly. Production has no
    // alternate table decoder; these helpers keep native assertions readable.
    internal static class NativeObservationAssertions
    {
        internal static object Data(object encoded)
        {
            var value = Wire.Object(encoded);
            object metadata;
            if (value.TryGetValue("metadata", out metadata))
                foreach (var pair in Wire.Array(metadata).Select(Wire.Array))
                    if (Equals(pair[0], "enum_name") || Equals(pair[0], "toolkit_value"))
                        return Data(pair[1]);
            var record = value["data"] as Dictionary<string, object>;
            if (record == null)
                return value["data"];
            if (record.ContainsKey("fields"))
                return Wire.Array(record["fields"]).Select(Wire.Array).ToDictionary(p => (string)p[0], p => Data(p[1]));
            if (record.ContainsKey("items"))
                return Wire.Array(record["items"]).Select(Data).ToArray();
            return record;
        }

        internal static Dictionary<string, object>[] Properties(Dictionary<string, object> node)
        {
            return node["properties"] == null ? new Dictionary<string, object>[0] : Wire.Array(node["properties"]).Select(Wire.Object).ToArray();
        }

        internal static Dictionary<string, object> Property(Dictionary<string, object> node, string name)
        {
            return Properties(node).Single(p => Equals(Wire.Array(p["key"])[1], name));
        }

        internal static Dictionary<string, object> Fields(Dictionary<string, object> node)
        {
            return Properties(node).Where(p => p["value"] != null && Equals(Wire.Array(p["key"])[0], "windows.uia")).ToDictionary(p => (string)Wire.Array(p["key"])[1], p => Data(p["value"]));
        }

        internal static Dictionary<string, object> Node(Dictionary<string, object> graph, string automationId)
        {
            return Wire.Array(graph["nodes"]).Select(Wire.Object).First(node =>
            {
                var fields = Fields(node);
                return fields.ContainsKey("AutomationId") && Equals(fields["AutomationId"], automationId);
            });
        }

        internal static object[] Targets(Dictionary<string, object> graph)
        {
            var root = Wire.Object(Wire.Array(graph["nodes"])[0]);
            var relation = Properties(root).Single(p => p.ContainsKey("read") && Wire.Array(Wire.Object(p["read"])["details"]).Select(Wire.Array).Any(pair => Equals(pair[0], "relation") && Equals(Data(pair[1]), true)));
            return Wire.Array(Wire.Object(Wire.Object(relation["value"])["data"])["items"]).Select(value => Wire.Object(Wire.Object(value)["data"])["ref"]).ToArray();
        }

        internal static object RelationDetail(Dictionary<string, object> graph, string key)
        {
            var root = Wire.Object(Wire.Array(graph["nodes"])[0]);
            return Data(Wire.Array(Wire.Object(Properties(root).Single()["read"])["details"]).Select(Wire.Array).Single(p => Equals(p[0], key))[1]);
        }
    }
}
