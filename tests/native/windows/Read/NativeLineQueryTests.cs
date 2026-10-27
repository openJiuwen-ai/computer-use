using System;
using System.Collections.Generic;

namespace ComputerUse.Windows
{
    internal static class NativeLineQueryTests
    {
        static int assertions;
        static void Assert(bool value, string detail)
        {
            assertions++;
            if (!value)
                throw new InvalidOperationException(detail);
        }

        static void Reject(Dictionary<string, object> value)
        {
            bool rejected = false;
            try
            {
                NativeQuerySchema.Validate(value);
            }
            catch (Failure)
            {
                rejected = true;
            }

            Assert(rejected, "invalid primary field contract accepted");
        }

        static Dictionary<string, object> Query(object fields, object primary)
        {
            return Wire.Obj("kind", "nodes", "fields", fields, "maxNodes", 1, "maxDepth", 0, "primaryFields", primary);
        }

        static void Main()
        {
            Func<Dictionary<string, object>, int, bool> eligible = NativeReadBackend.PrimaryTextCandidate;
            Assert(!eligible(Wire.Obj(), 0), "unknown visibility became a primary probe");
            Assert(!eligible(Wire.Obj("IsOffscreen", true, "ControlType", "ControlType.Document"), 0), "offscreen document was probed by default");
            Assert(eligible(Wire.Obj("IsOffscreen", false), 0), "explicit visible root omitted");
            Assert(!eligible(Wire.Obj("IsOffscreen", false), 1), "ordinary descendant became a primary probe");
            Assert(eligible(Wire.Obj("IsOffscreen", false, "ControlType", "ControlType.Document"), 2), "visible document omitted");
            Assert(eligible(Wire.Obj("IsOffscreen", false, "HasKeyboardFocus", true), 2), "visible focused object omitted");
            Assert(!eligible(Wire.Obj("IsOffscreen", false, "HasKeyboardFocus", false, "ControlType", "ControlType.Edit"), 2), "unfocused ordinary editor probed");
            Assert(!eligible(Wire.Obj("IsOffscreen", "false", "HasKeyboardFocus", true), 0), "malformed visibility accepted");
            NativeQuerySchema.Validate(Query(new[] { "Text.LineRanges" }, new[] { "Text.LineRanges" }));
            Assert(true, "line field rejected");
            Reject(Query(new[] { "Name" }, new[] { "Text.LineRanges" }));
            Reject(Query(new[] { "Name" }, new[] { "Name" }));
            Reject(Query(new[] { "Text.LineRanges" }, new string[0]));
            Reject(Query(new[] { "Text.LineRanges" }, new[] { "Text.LineRanges", "Text.LineRanges" }));
            Reject(Query(new[] { "Text.CaretRange" }, new[] { "Text.CaretRange" }));
            Console.WriteLine(Wire.Json(Wire.Obj("assertions", assertions, "scope", "production query and cached primary-scope contract; no GUI claim")));
        }
    }
}
