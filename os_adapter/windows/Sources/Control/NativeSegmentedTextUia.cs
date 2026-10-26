using System;
using System.Globalization;
using System.Linq;
using System.Text.RegularExpressions;
using System.Windows.Automation;
using System.Windows.Automation.Text;

namespace ComputerUse.Windows
{
    // Segmented editors validate each section while typing. A selection across
    // several sections is not an ordinary text field, even when UIA exposes a
    // full document range. Choose this recipe before the first mutation.
    internal sealed partial class NativeUiaKeyboard : INativeKeyboardReplacementProvider
    {
        sealed class SegmentedReplacement : NativeTextReplacement
        {
            readonly NativeUiaKeyboard owner;
            readonly NativeKeyboardState state;
            readonly string requested, separators;
            readonly string[] parts;
            readonly bool date;
            static readonly Regex Date = new Regex(@"^[0-9]{4}([/.-])[0-9]{1,2}\1[0-9]{1,2}$", RegexOptions.CultureInvariant);
            static readonly Regex Time = new Regex(@"^[0-9]{1,2}:[0-9]{2}(:[0-9]{2})?$", RegexOptions.CultureInvariant);
            static readonly Regex DateTimeLiteral = new Regex(@"^(?:[0-9]{1,4}([/.-])[0-9]{1,2}\1[0-9]{1,4}(?: [0-9]{1,2}:[0-9]{2}(?::[0-9]{2})?)?|[0-9]{1,2}:[0-9]{2}(?::[0-9]{2})?)$", RegexOptions.CultureInvariant);
            internal static bool LooksLikeDateTime(string text)
            {
                return DateTimeLiteral.IsMatch(text);
            }

            internal static string Kind(string text)
            {
                var pair = text.Split(' ');
                return Date.IsMatch(text) || pair.Length == 2 && Date.IsMatch(pair[0]) && Time.IsMatch(pair[1]) ? "date" : Time.IsMatch(text) ? "time" : null;
            }

            internal static bool Valid(string value, bool date)
            {
                if (date)
                {
                    var pair = value.Split(' ');
                    if (pair.Length == 2)
                        return Valid(pair[0], true) && Valid(pair[1], false);
                    if (!Date.IsMatch(value))
                        return false;
                    DateTime parsed;
                    return DateTime.TryParseExact(value.Replace('-', '/').Replace('.', '/'), new[] { "yyyy/MM/dd", "yyyy/M/dd", "yyyy/MM/d", "yyyy/M/d" }, CultureInfo.InvariantCulture, DateTimeStyles.None, out parsed);
                }

                if (!Time.IsMatch(value))
                    return false;
                var numbers = value.Split(':').Select(Int32.Parse).ToArray();
                return numbers[0] < 24 && numbers[1] < 60 && (numbers.Length == 2 || numbers[2] < 60);
            }

            internal SegmentedReplacement(NativeUiaKeyboard owner, NativeKeyboardState state, string current, string requested, bool date)
            {
                this.owner = owner;
                this.state = state;
                this.requested = requested;
                this.date = date;
                separators = Regex.Replace(current, @"\d+", "#");
                if (!Valid(requested, date) || Regex.Replace(requested, @"\d+", "#") != separators)
                    throw new Failure("invalid_request", "replacement must be a valid date/time in the observed editor format; no input sent");
                parts = Regex.Matches(requested, @"\d+").Cast<Match>().Select(m => m.Value).ToArray();
            }

            string Current(Request r)
            {
                var text = owner.TextPatternFor(state, r).DocumentRange.GetText(128);
                r.Check();
                text = text.TrimEnd('\r', '\n');
                if (!Valid(text, date) || Regex.Replace(text, @"\d+", "#") != separators)
                    throw new Failure("input_unavailable", "the addressed segmented editor changed its format or became unreadable");
                return text;
            }

            internal override int Count
            {
                get
                {
                    return parts.Length;
                }
            }

            internal override NativeTextSegment Segment(int index, Request r)
            {
                string text = Current(r);
                var match = Regex.Matches(text, @"\d+")[index];
                if (match.Value == parts[index])
                    return null;
                var pattern = owner.TextPatternFor(state, r);
                var document = pattern.DocumentRange;
                if (document.GetText(128).TrimEnd('\r', '\n') != text)
                    throw new Failure("input_unavailable", "segmented editor text changed while selecting its range");
                var range = document.Clone();
                range.MoveEndpointByRange(TextPatternRangeEndpoint.End, document, TextPatternRangeEndpoint.Start);
                if (range.MoveEndpointByUnit(TextPatternRangeEndpoint.End, TextUnit.Character, match.Index + match.Length) != match.Index + match.Length || range.MoveEndpointByUnit(TextPatternRangeEndpoint.Start, TextUnit.Character, match.Index) != match.Index || range.GetText(128) != match.Value)
                    throw new Failure("input_unavailable", "the editor's character ranges do not match its observed segment");
                return new NativeTextSegment
                {
                    Text = parts[index],
                    Select = () =>
                    {
                        r.Check();
                        range.Select();
                        r.Check();
                    },
                    Progress = position =>
                    {
                        string current = owner.TextPatternFor(state, r).DocumentRange.GetText(128).TrimEnd('\r', '\n');
                        r.Check();
                        if (Regex.Replace(current, @"\d+", "#") != separators)
                            return false;
                        var numbers = Regex.Matches(current, @"\d+");
                        return numbers.Count == parts.Length && Int32.Parse(numbers[index].Value, CultureInfo.InvariantCulture) == Int32.Parse(parts[index].Substring(0, position), CultureInfo.InvariantCulture);
                    },
                    Selected = () =>
                    {
                        r.Check();
                        var selected = owner.TextPatternFor(state, r).GetSelection();
                        r.Check();
                        return selected.Length == 1 && selected[0].CompareEndpoints(TextPatternRangeEndpoint.Start, range, TextPatternRangeEndpoint.Start) == 0 && selected[0].CompareEndpoints(TextPatternRangeEndpoint.End, range, TextPatternRangeEndpoint.End) == 0;
                    },
                    Applied = () => Regex.Matches(Current(r), @"\d+")[index].Value == parts[index]
                };
            }

            internal override bool Applied(Request r)
            {
                return Current(r) == requested;
            }
        }

        public NativeTextReplacement Replacement(NativeKeyboardState state, string text, Request r)
        {
            if ((state.EditorRole ?? state.Role) != "ControlType.Spinner")
                return null;
            var document = TextPatternFor(state, r).DocumentRange;
            string current = document.GetText(128).TrimEnd('\r', '\n');
            r.Check();
            string kind = SegmentedReplacement.Kind(current);
            if (kind == null && SegmentedReplacement.LooksLikeDateTime(text))
                throw new Failure("input_unavailable", "the addressed spinner does not expose a supported year-first date or 24-hour time format; no input sent");
            return kind == null ? null : new SegmentedReplacement(this, state, current, text, kind == "date");
        }
    }
}
