using System;
using System.Collections.Generic;
using System.Windows.Automation.Text;

namespace ComputerUse.Windows
{
    // A supplemental, passive reading view. Native line units may represent
    // layout absent from GetText, or fall back to a larger provider unit. They
    // never rewrite the immutable body, establish caret state or grant input.
    internal sealed partial class NativeReadText
    {
        internal object LineRanges()
        {
            CheckIdentity();
            var anchor = PositionAnchor();
            var records = new List<object>();
            var observedRanges = new List<UiaTextRange>();
            string state = "partial", detail = "At most 32 native line-unit spans; providers may substitute a larger unit.";
            bool reachedEnd = false, completePreview = true, coveredText = true;
            int coveredEnd = 0;
            int remaining = 16384;
            var line = Document.Clone();
            line.MoveEndpointByRange(TextPatternRangeEndpoint.End, line, TextPatternRangeEndpoint.Start);
            check();
            line.ExpandToEnclosingUnit(TextUnit.Line);
            check();
            UiaTextRange previous = null;
            for (int i = 0; i < 32; i++)
            {
                check();
                var record = Wire.Obj("values", Wire.Obj(), "fieldStatus", Wire.Obj());
                var errors = Wire.Object(record["fieldStatus"]);
                try
                {
                    if (line.CompareEndpoints(TextPatternRangeEndpoint.Start, Document, TextPatternRangeEndpoint.End) > 0 || line.CompareEndpoints(TextPatternRangeEndpoint.End, Document, TextPatternRangeEndpoint.Start) < 0)
                    {
                        detail = "Native line movement left the original document; no outside text was read.";
                        break;
                    }

                    // Clamp before any range text/prefix read. Move can cross
                    // the original document into adjacent controls in a host.
                    var part = line.Clone();
                    check();
                    observedRanges.Add(part);
                    if (part.CompareEndpoints(TextPatternRangeEndpoint.Start, Document, TextPatternRangeEndpoint.Start) < 0)
                        part.MoveEndpointByRange(TextPatternRangeEndpoint.Start, Document, TextPatternRangeEndpoint.Start);
                    if (part.CompareEndpoints(TextPatternRangeEndpoint.End, Document, TextPatternRangeEndpoint.End) > 0)
                        part.MoveEndpointByRange(TextPatternRangeEndpoint.End, Document, TextPatternRangeEndpoint.End);
                    check();
                    if (previous != null && (part.CompareEndpoints(TextPatternRangeEndpoint.Start, previous, TextPatternRangeEndpoint.End) < 0 || part.CompareEndpoints(TextPatternRangeEndpoint.End, previous, TextPatternRangeEndpoint.End) <= 0))
                        throw new NativeFieldUnavailable("error", "Native line units repeated or overlapped a previous span");
                    var position = Position(part, anchor);
                    // Distinct provider endpoints can map to the same plain-
                    // text offset (e.g. block markup). Native gaps are allowed;
                    // missing source characters stay explicitly unsegmented.
                    if ((int)position["start"] < coveredEnd)
                        throw new NativeFieldUnavailable("error", "Native line units overlap in the captured text");
                    if ((int)position["start"] != coveredEnd)
                        coveredText = false;
                    coveredEnd = (int)position["end"];
                    string text = anchor.Text.Substring((int)position["start"], (int)position["end"] - (int)position["start"]);
                    int length = Math.Min(text.Length, remaining);
                    if (length > 0 && Char.IsHighSurrogate(text[length - 1]))
                        length--;
                    position["text"] = text.Substring(0, length);
                    remaining -= length;
                    if (length != text.Length)
                    {
                        completePreview = false;
                        errors["text"] = Wire.Obj("state", "truncated", "detail", "Shared 16384 UTF-16 line preview budget exhausted; positions retain their native range.");
                    }

                    record["values"] = position;
                    reachedEnd = part.CompareEndpoints(TextPatternRangeEndpoint.End, Document, TextPatternRangeEndpoint.End) == 0;
                    check();
                    records.Add(record);
                    previous = part;
                    if (reachedEnd)
                    {
                        state = anchor.Complete && completePreview && coveredText ? "complete" : "partial";
                        if (!coveredText)
                            detail = "Some original text is outside the returned native line units; it remains in the unchanged document body.";
                        break;
                    }

                    if (remaining == 0)
                    {
                        detail = "Line preview budget exhausted before document end; the remaining layout is unread.";
                        break;
                    }

                    var before = line.Clone();
                    int moved = line.Move(TextUnit.Line, 1);
                    check();
                    if (moved <= 0 || line.CompareEndpoints(TextPatternRangeEndpoint.Start, before, TextPatternRangeEndpoint.Start) <= 0)
                    {
                        detail = "Native line movement stopped before document end; the remaining layout is unconfirmed.";
                        break;
                    }
                }
                catch (Exception error)
                {
                    errors["position"] = NativeReadBackend.Status(error);
                    if (!records.Contains(record))
                        records.Add(record);
                    detail = "A native line-unit span could not be confirmed; the remaining layout is unread.";
                    break;
                }
            }

            var result = Wire.Obj("source", Source + ".TextUnit.Line", "requestedUnit", "Line", "unitSupport", "unconfirmed", "anchor", AnchorFacts(anchor), "ranges", records, "state", state, "documentEndReached", reachedEnd, "detail", detail);
            ObservedRanges["Text.LineRanges"] = observedRanges.ToArray();
            try
            {
                VerifyAnchor(anchor);
            }
            catch (Exception error)
            {
                result["state"] = "partial";
                result["documentEndReached"] = false;
                result["anchorStatus"] = NativeReadBackend.Status(error);
                foreach (Dictionary<string, object> record in records)
                {
                    record["values"] = Wire.Obj();
                    Wire.Object(record["fieldStatus"])["position"] = result["anchorStatus"];
                }
            }

            CheckIdentity();
            return result;
        }
    }
}
