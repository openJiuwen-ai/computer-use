using System;
using System.Collections.Generic;
using System.Linq;
using System.Security.Cryptography;
using System.Text;
using System.Windows.Automation;
using System.Windows.Automation.Text;
using UIA = NativeUIA;

namespace ComputerUse.Windows
{
    // Passive range observations. Only clones' endpoints move: no Select,
    // focus, scrolling, realization or actions. Offsets count GetText UTF-16,
    // never TextUnit.Character or the magnitude of CompareEndpoints.
    internal sealed partial class NativeReadText
    {
        readonly UiaElement element;
        string capturedBody;
        bool capturedBodyComplete;
        const int PositionUnits = 65536, ObjectLimit = 32, PrefixBudget = 262144;
        sealed class Anchor
        {
            internal string Text;
            internal bool Complete;
            internal int Remaining = PrefixBudget;
        }

        static string Hash(string value)
        {
            using (var hash = SHA256.Create())
                return BitConverter.ToString(hash.ComputeHash(Encoding.UTF8.GetBytes(value))).Replace("-", "").ToLowerInvariant();
        }

        Anchor PositionAnchor()
        {
            CheckIdentity();
            string text = capturedBody;
            bool complete = capturedBodyComplete;
            if (text == null)
            {
                text = Document.GetText(PositionUnits + 1);
                check();
                complete = text != null && text.Length <= PositionUnits;
            }

            if (text == null)
                throw new NativeFieldUnavailable("absent", "No document text for range positions");
            int end = Math.Min(text.Length, PositionUnits);
            if (end > 0 && Char.IsHighSurrogate(text[end - 1]))
                end--;
            return new Anchor
            {
                Text = text.Substring(0, end),
                Complete = complete && end == text.Length
            };
        }

        Dictionary<string, object> AnchorFacts(Anchor anchor)
        {
            return Wire.Obj("source", "Text.DocumentRange", "unit", "utf16", "units", anchor.Text.Length, "complete", anchor.Complete, "sha256", Hash(anchor.Text));
        }

        void VerifyAnchor(Anchor anchor)
        {
            CheckIdentity();
            // Exact bounded prefix, plus a terminator probe for a complete
            // document. A changed observation never becomes a stale offset.
            string current = Document.GetText(anchor.Text.Length + (anchor.Complete ? 1 : 0));
            check();
            if (current != anchor.Text)
                throw new NativeFieldUnavailable("error", "Document changed while locating text ranges; positions are unconfirmed", current);
        }

        string Prefix(UiaTextRange range, TextPatternRangeEndpoint endpoint, Anchor anchor)
        {
            check();
            var prefix = Document.Clone();
            prefix.MoveEndpointByRange(TextPatternRangeEndpoint.End, range, endpoint);
            check();
            int limit = Math.Min(anchor.Text.Length + 1, Math.Max(0, anchor.Remaining));
            if (limit < 1)
                throw new NativeFieldUnavailable("truncated", "Shared text-position prefix budget exhausted");
            string value = prefix.GetText(limit);
            check();
            if (value == null)
                throw new NativeFieldUnavailable("absent", "UIA returned no prefix text");
            anchor.Remaining -= value.Length;
            if (value.Length >= limit || value.Length > anchor.Text.Length)
                throw new NativeFieldUnavailable("truncated", "Range position exceeds the verified document prefix or prefix budget");
            if (!anchor.Text.StartsWith(value, StringComparison.Ordinal))
                throw new NativeFieldUnavailable("error", "Range prefix disagrees with the captured document", value);
            return value;
        }

        Dictionary<string, object> Position(UiaTextRange range, Anchor anchor)
        {
            check();
            if (range == null)
                throw new NativeFieldUnavailable("absent", "UIA returned no text range");
            var value = range.Clone();
            check();
            if (value.CompareEndpoints(TextPatternRangeEndpoint.Start, value, TextPatternRangeEndpoint.End) > 0 || value.CompareEndpoints(TextPatternRangeEndpoint.Start, Document, TextPatternRangeEndpoint.Start) < 0 || value.CompareEndpoints(TextPatternRangeEndpoint.End, Document, TextPatternRangeEndpoint.End) > 0)
                throw new NativeFieldUnavailable("error", "Text range is reversed or outside its document");
            string start = Prefix(value, TextPatternRangeEndpoint.Start, anchor);
            string end = Prefix(value, TextPatternRangeEndpoint.End, anchor);
            if (end.Length < start.Length)
                throw new NativeFieldUnavailable("error", "Range endpoints disagree with text lengths");
            if (start.Length < anchor.Text.Length && Char.IsLowSurrogate(anchor.Text[start.Length]) || end.Length < anchor.Text.Length && Char.IsLowSurrogate(anchor.Text[end.Length]))
                throw new NativeFieldUnavailable("error", "UIA text endpoint splits a UTF-16 surrogate pair");
            string text = value.GetText(Math.Min(PositionUnits + 1, end.Length - start.Length + 1));
            check();
            if (text != anchor.Text.Substring(start.Length, end.Length - start.Length))
                throw new NativeFieldUnavailable("error", "Range text disagrees with captured document endpoints", text);
            return Wire.Obj("start", start.Length, "end", end.Length, "degenerate", value.CompareEndpoints(TextPatternRangeEndpoint.Start, value, TextPatternRangeEndpoint.End) == 0);
        }

        sealed class ObjectRange
        {
            internal Dictionary<string, object> Record;
            internal UiaTextRange Range;
        }

        internal object ObjectRanges(Func<UiaElement, string> retain)
        {
            CheckIdentity();
            if (Source != "IUIAutomation.TextPattern")
                throw new NativeFieldUnavailable("unsupported", "Legacy text supplement cannot bind native embedded-object positions");
            var anchor = PositionAnchor();
            var children = Children();
            if (children == null)
                throw new NativeFieldUnavailable("absent", "UIA returned no embedded-object array");
            var objects = new List<ObjectRange>();
            string state = children.Length > ObjectLimit ? "partial" : "complete";
            string order = "nativeEndpoints";
            Dictionary<string, object> orderStatus = null;
            foreach (var child in children.Take(ObjectLimit))
            {
                check();
                var record = Wire.Obj("values", Wire.Obj(), "fieldStatus", Wire.Obj());
                var item = new ObjectRange
                {
                    Record = record
                };
                try
                {
                    if (child == null)
                        throw new NativeFieldUnavailable("error", "Embedded object has no native identity");
                    record["node"] = retain(child);
                    CheckIdentity();
                    Trace("RangeFromChild");
                    item.Range = pattern.RangeFromChild(child);
                    check();
                    record["values"] = Position(item.Range, anchor);
                }
                catch (Exception error)
                {
                    Wire.Object(record["fieldStatus"])["position"] = NativeReadBackend.Status(error);
                    state = "partial";
                }

                objects.Add(item);
            }

            // An invalid/unknown range does not disappear or acquire a guessed
            // order. Keep all references and the provider's membership order.
            if (objects.Any(item => Wire.Object(item.Record["fieldStatus"]).Count != 0))
                order = "unconfirmed";
            else
                try
                {
                    var sorted = new List<ObjectRange>(objects);
                    // At most 32 objects; insertion sort permits deadline checks
                    // and propagates native COM failures without List.Sort wrapping.
                    for (int i = 1; i < sorted.Count; i++)
                    {
                        int j = i;
                        var item = sorted[i];
                        while (j > 0)
                        {
                            check();
                            int compare = sorted[j - 1].Range.CompareEndpoints(TextPatternRangeEndpoint.Start, item.Range, TextPatternRangeEndpoint.Start);
                            check();
                            if (compare <= 0)
                                break;
                            sorted[j] = sorted[j - 1];
                            j--;
                        }

                        sorted[j] = item;
                    }

                    for (int i = 1; i < sorted.Count; i++)
                    {
                        check();
                        if (sorted[i - 1].Range.CompareEndpoints(TextPatternRangeEndpoint.End, sorted[i].Range, TextPatternRangeEndpoint.Start) > 0)
                            order = "overlapping";
                    }

                    objects = sorted;
                }
                catch (Exception error)
                {
                    orderStatus = NativeReadBackend.Status(error);
                    order = "unconfirmed";
                    state = "partial";
                }

            var result = Wire.Obj("source", Source + ".RangeFromChild", "anchor", AnchorFacts(anchor), "objects", objects.Select(item => item.Record).ToArray(), "total", children.Length, "state", state, "order", order);
            if (children.Length > ObjectLimit)
                result["detail"] = "At most 32 embedded-object positions; Text.Children retains remaining native references.";
            if (orderStatus != null)
                result["orderStatus"] = orderStatus;
            ObservedRanges["Text.ObjectRanges"] = objects.Select(item => item.Range).ToArray();
            try
            {
                VerifyAnchor(anchor);
            }
            catch (Exception error)
            {
                result["state"] = "partial";
                result["order"] = "unconfirmed";
                result["anchorStatus"] = NativeReadBackend.Status(error);
                // Do not expose values as confirmed when their anchor changed.
                foreach (var item in objects)
                {
                    item.Record["values"] = Wire.Obj();
                    Wire.Object(item.Record["fieldStatus"])["position"] = result["anchorStatus"];
                }
            }

            CheckIdentity();
            return result;
        }

        internal object CaretRange(bool focusedOnly)
        {
            CheckIdentity();
            if (focusedOnly && element.GetCurrentPropertyValue(AutomationElement.HasKeyboardFocusProperty, true) as bool? != true)
                return Wire.Obj("state", "notProbed", "detail", "Default caret observation probes only an object that reports keyboard focus; explicit field queries can probe inactive carets.");
            if (Source != "IUIAutomation.TextPattern")
                throw new NativeFieldUnavailable("unsupported", "TextPattern2 is not available through the legacy text supplement");
            var raw = UiaClient.Call(() => element.Native.GetCurrentPattern(10024)) as UIA.IUIAutomationTextPattern2;
            check();
            if (raw == null)
                throw new NativeFieldUnavailable("unsupported", "UIA does not expose TextPattern2");
            int active;
            var native = raw.GetCaretRange(out active);
            check();
            var range = native == null ? null : new UiaTextRange(native);
            ObservedRanges["Text.CaretRange"] = new[]
            {
                range
            };
            var values = Wire.Obj("isActive", active != 0);
            var statuses = Wire.Obj();
            try
            {
                var anchor = PositionAnchor();
                values["anchor"] = AnchorFacts(anchor);
                var position = Position(range, anchor);
                if (!(bool)position["degenerate"] || !Equals(position["start"], position["end"]))
                    throw new NativeFieldUnavailable("error", "TextPattern2 returned a non-degenerate caret range");
                values["range"] = position;
                VerifyAnchor(anchor);
            }
            catch (Exception error)
            {
                values.Remove("range");
                statuses["range"] = NativeReadBackend.Status(error);
            }

            CheckIdentity();
            return Wire.Obj("source", "IUIAutomationTextPattern2.GetCaretRange", "values", values, "fieldStatus", statuses, "state", statuses.Count == 0 ? "complete" : "partial");
        }
    }
}
