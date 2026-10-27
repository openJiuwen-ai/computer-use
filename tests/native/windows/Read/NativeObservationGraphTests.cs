using System;
using System.Collections.Generic;
using System.Linq;
using System.Text.Json;

namespace System.Web.Script.Serialization
{
    public sealed class JavaScriptSerializer
    {
        public int MaxJsonLength { get; set; }
        public int RecursionLimit { get; set; }

        public string Serialize(object value)
        {
            return JsonSerializer.Serialize(value);
        }

        public object DeserializeObject(string value)
        {
            throw new NotSupportedException();
        }
    }
}

namespace ComputerUse.Windows
{
    internal static class NativeObservationGraphTests
    {
        static int assertions;
        static void Assert(bool value, string message)
        {
            assertions++;
            if (!value)
                throw new InvalidOperationException(message);
        }

        static Dictionary<string, object> Property(Dictionary<string, object> node, string name)
        {
            return Wire.Array(node["properties"]).Select(Wire.Object).Single(p => Equals(Wire.Array(p["key"])[1], name));
        }

        static object Payload(Dictionary<string, object> property)
        {
            return Wire.Object(property["value"])["data"];
        }

        static int Main()
        {
            var bounds = Wire.Obj("x", -1920.5, "y", -10.25, "width", 0.0, "height", 32.5);
            var fields = new[]
            {
                "ControlType",
                "BoundingRectangle",
                "Unknown.Fact",
                "CachedProperty",
                "Unsupported",
                "NotRead",
                "Malformed",
                "NativeNull",
                "PartialText",
                "Special",
                "NegativeZero",
                "UTF16",
                "Decimal"
            };
            var values = Wire.Obj("ControlType", "ControlType.Button", "BoundingRectangle", bounds, "Unknown.Fact", new object[] { false, 0, "", new NativeObservationReference("outside") }, "NativeNull", null, "PartialText", "prefix", "Special", Double.NaN, "NegativeZero", BitConverter.Int64BitsToDouble(Int64.MinValue), "UTF16", "prefix\ud800suffix\udfff", "Decimal", Decimal.MaxValue);
            var calls = new List<string>();
            var cachedFailures = new List<object>();
            var badCachedValue = new[]
            {
                1.0,
                2.0
            };
            values["CachedProperty"] = NativeCachedPropertyGate.Read(() =>
            {
                calls.Add("cached");
                var error = new InvalidOperationException("invalid cached rectangle");
                error.Data["nativeRawResult"] = badCachedValue;
                throw error;
            }, () =>
            {
                calls.Add("current");
                return bounds;
            }, error =>
            {
                calls.Add("record");
                cachedFailures.Add(Wire.Obj("state", "error", "cached", true, "api", "IUIAutomationElement.GetCachedPropertyValueEx", "propertyId", 30001, "hresult", error.HResult, "rawResult", error.Data["nativeRawResult"]));
            }, () => calls.Add("check"));
            Assert(calls.SequenceEqual(new[] { "cached", "check", "record", "current" }), "Cached fallback added or reordered native reads");
            Assert(Object.ReferenceEquals(Wire.Object(cachedFailures[0])["rawResult"], badCachedValue) && Object.ReferenceEquals(values["CachedProperty"], bounds), "One of the two acquired results was discarded");
            int unwanted = 0;
            Assert(Equals(NativeCachedPropertyGate.Read(() => "cached", () =>
            {
                unwanted++;
                return null;
            }, error => unwanted++, () => unwanted++), "cached") && unwanted == 0, "Cached success triggered an extra read or error callback");
            var unrelated = new FormatException("provider error");
            try
            {
                NativeCachedPropertyGate.Read(() =>
                {
                    throw unrelated;
                }, () =>
                {
                    unwanted++;
                    return null;
                }, error => unwanted++, () => unwanted++);
            }
            catch (FormatException error)
            {
                Assert(Object.ReferenceEquals(error, unrelated) && unwanted == 0, "The cache gate swallowed another failure kind");
            }

            var statuses = Wire.Obj("Unsupported", Wire.Obj("state", "unsupported", "nativeError", Wire.Obj("hresult", unchecked((int)0x80040204))), "NotRead", Wire.Obj("state", "notProbed", "detail", "query plan"), "Malformed", Wire.Obj("state", "error", "rawResult", new object[] { Double.NaN, new object (), new object () }), "PartialText", Wire.Obj("state", "truncated", "textRef", "captured", "totalUnits", 99));
            var rows = new[]
            {
                Wire.Obj("node", "root", "requestedFields", fields, "values", values, "fieldStatus", statuses, "actions", Wire.Obj("state", "complete", "items", new[] { Wire.Obj("name", "Invoke.Invoke", "description", "Invoke.Invoke") }, "total", 1))
            };
            var native = new Dictionary<string, Dictionary<string, object>>
            {
                {
                    "root",
                    Wire.Obj("ControlType", new NativeObservationValue("UIA.ControlType", 50000, NativeObservationGraph.Pair("enum_name", "ControlType.Button")), "BoundingRectangle", new NativeObservationValue("UIA.Rect", bounds, NativeObservationGraph.Pair("unit", "physical_pixel")), "PartialText", new NativeObservationValue("BSTR", "prefix", NativeObservationGraph.Pair("native_range", new NativeObservationValue("IUIAutomationTextRange", new NativeObservationReference("range")))))
                }
            };
            var resource = Wire.Obj("id", "range", "source", "windows.uia", "kind", "text_range", "nativeType", "IUIAutomationTextRange", "properties", null);
            var resources = new List<Dictionary<string, object>>
            {
                resource
            };
            int retained = 0;
            var edges = new[]
            {
                Wire.Obj("source", "root", "property", "RawViewChildren", "targets", new[] { "child", "child", "root" }, "certainty", "confirmed", "state", "partial", "offset", 2, "total", 9, "cursor", "next"),
                Wire.Obj("source", "root", "property", "RawViewParent", "targets", new string[0], "certainty", "unknown", "state", "unsupported")
            };
            var graph = NativeObservationGraph.Encode("session", "surface", Wire.Obj("id", "observation", "startedAt", 10, "completedAt", 11), new[] { "root" }, rows, edges, new[] { "child" }, new[] { "depth budget" }, new[] { "RawViewChildren", "RawViewParent" }, "change", native, resources, id => id == "root" ? new NativeObservationValue("UIA.RuntimeId", new[] { 42, 5, -8 }) : null, value =>
            {
                if (retained++ != 0)
                    return null;
                resources.Add(Wire.Obj("id", "opaque", "source", "windows.uia", "kind", "native_value", "nativeType", value.GetType().FullName, "properties", null));
                return new NativeObservationReference("opaque");
            }, cachedFailures);
            Assert((string)graph["kind"] == "observation", "Only the unified observation may cross the wire");
            Assert(!graph.ContainsKey("relations") && !graph.ContainsKey("unread") && !graph.ContainsKey("observed"), "Legacy graph leaked");
            var nodes = Wire.Array(graph["nodes"]).Select(Wire.Object).ToDictionary(node => (string)node["id"]);
            Assert(nodes.Count == 5 && nodes["child"]["properties"] == null && nodes["outside"]["properties"] == null, "Every reference needs one declaration");
            Assert((string)nodes["range"]["kind"] == "text_range", "Native text resource became an element");
            Assert((string)nodes["opaque"]["kind"] == "native_value" && retained == 2, "Opaque values acquired element input authority or lost budget evidence");
            var root = nodes["root"];
            Assert(Equals(Payload(Property(root, "ControlType")), 50000), "UIA enum lost its original numeric value");
            var geometry = Wire.Object(Payload(Property(root, "BoundingRectangle")));
            var geometryFields = Wire.Array(geometry["fields"]).Select(Wire.Array).ToDictionary(p => (string)p[0], p => Wire.Object(p[1])["data"]);
            Assert(Equals(geometryFields["x"], -1920.5) && Equals(geometryFields["width"], 0.0), "Geometry was rounded or zero discarded");
            Assert(Property(root, "NativeNull")["value"] != null && Payload(Property(root, "NativeNull")) == null, "Native null became a failed read");
            Assert(Property(root, "Unsupported")["value"] == null && (string)Property(root, "Unsupported")["state"] == "unsupported", "Unsupported became a default value");
            Assert(Property(root, "NotRead")["value"] == null && (string)Property(root, "NotRead")["state"] == "not_read", "A skipped query became a provider value");
            Assert((string)Property(root, "PartialText")["state"] == "value" && Equals(Wire.Object(Property(root, "PartialText")["read"])["complete"], false), "Partial payload was dropped");
            var relation = Property(root, "RawViewChildren");
            var members = Wire.Array(Wire.Object(Payload(relation))["items"]).Select(Wire.Object).Select(v => (string)Wire.Object(v["data"])["ref"]).ToArray();
            Assert(members.SequenceEqual(new[] { "child", "child", "root" }), "Native relation order, repetitions or cycle changed");
            Assert((string)Wire.Object(Payload(Property(root, "Special")))["float"] == "nan", "NaN lost its exact tagged value");
            Assert((string)Wire.Object(Payload(Property(root, "NegativeZero")))["float"] == "-0", "Negative zero lost its sign");
            Assert((string)Wire.Object(Property(root, "UTF16")["value"])["nativeType"] == "System.String", "Original string type was discarded");
            var originalUnits = Convert.FromBase64String((string)Wire.Object(Payload(Property(root, "UTF16")))["bytes"]);
            Assert(originalUnits.Length == 28 && originalUnits[13] == 0xd8 && originalUnits[27] == 0xdf, "Unpaired UTF-16 units were replaced");
            Assert(Wire.Array(Wire.Object(Payload(Property(root, "Decimal")))["fields"]).Length == 4, "Decimal native bits were rounded through JSON");
            Assert(Wire.Array(root["properties"]).Select(Wire.Object).Select(p => Wire.Array(p["key"])[1]).Take(fields.Length).SequenceEqual(fields), "Property request order changed");
            using (JsonDocument.Parse(JsonSerializer.Serialize(graph)))
            {
            }

            Console.WriteLine(JsonSerializer.Serialize(new { assertions = assertions, graph = graph }));
            return 0;
        }
    }
}
