using System;
using UIA = NativeUIA;
using System.Windows.Automation;
using System.Windows.Automation.Text;

namespace ComputerUse.Windows
{
    internal sealed partial class NativeUiaKeyboard
    {
        static bool? ReadOnly(UiaElement element, AutomationPattern kind, Request r)
        {
            try
            {
                r.Check();
                object raw;
                if (!element.TryGetCurrentPattern(kind, out raw))
                    return null;
                r.Check();
                object value = kind == ValuePattern.Pattern ? (object)(((UIA.IUIAutomationValuePattern)raw).CurrentIsReadOnly != 0) : ((UiaTextPattern)raw).DocumentRange.GetAttributeValue(TextPattern.IsReadOnlyAttribute);
                r.Check();
                return value as bool? ;
            }
            catch
            {
                r.Check();
                return null;
            } // This route stays unknown; an independent object unit may remain usable.
        }

        static void ReadTextCapability(UiaElement element, NativeKeyboardState state, Request r)
        {
            bool? valueReadOnly = ReadOnly(element, ValuePattern.Pattern, r), textReadOnly = ReadOnly(element, TextPattern.Pattern, r);
            state.TextWritable = valueReadOnly == true || textReadOnly == true ? false : valueReadOnly == false || textReadOnly == false ? (bool? )true : null;
            try
            {
                r.Check();
                object raw;
                state.TextSelection = element.TryGetCurrentPattern(TextPattern.Pattern, out raw) && ((UiaTextPattern)raw).SupportedTextSelection != SupportedTextSelection.None;
                r.Check();
            }
            catch
            {
                r.Check();
                state.TextSelection = false;
            }
        }

        sealed class Sequence : INativeKeyboardSequence, INativePasteClipboardSequence
        {
            readonly NativeUiaKeyboard owner;
            readonly Target target;
            readonly IntPtr focus;
            readonly uint pid;
            readonly long generation;
            readonly UiaElement element;
            readonly string runtime;
            readonly ControlType role;
            readonly string nativeClass;
            readonly bool multiline;
            uint? paragraphStyle, paragraphMode;
            bool RichMultiline
            {
                get
                {
                    return multiline && (nativeClass.Equals("RichEdit20W", StringComparison.OrdinalIgnoreCase) || nativeClass.Equals("RICHEDIT50W", StringComparison.OrdinalIgnoreCase) || nativeClass.StartsWith("WindowsForms10.RichEdit20W.", StringComparison.OrdinalIgnoreCase) || nativeClass.StartsWith("WindowsForms10.RICHEDIT50W.", StringComparison.OrdinalIgnoreCase));
                }
            }

            uint ReadMode(uint message, Request r)
            {
                r.Check();
                UIntPtr result;
                uint timeout = (uint)Math.Max(1, Math.Min(100, r.Deadline - Wire.Now));
                if (Native.SendMessageTimeoutRaw(focus, message, IntPtr.Zero, IntPtr.Zero, 2 | 32, timeout, out result) == IntPtr.Zero)
                    throw new Failure("input_unavailable", "the native rich editor paragraph mode could not be confirmed; no paste sent");
                r.Check();
                return (uint)result.ToUInt64();
            }

            public bool FinalParagraph(Request r)
            {
                if (!RichMultiline)
                    return false;
                Validate(r);
                // EM_GETEDITSTYLE / EM_GETTEXTMODE are read-only. Plain-text or
                // system-edit emulation already retains the final line break.
                paragraphStyle = ReadMode(0x4cd, r) & 1;
                paragraphMode = ReadMode(0x45a, r) & 3;
                Validate(r);
                if (paragraphMode != 1 && paragraphMode != 2)
                    throw new Failure("input_unavailable", "the native rich editor text mode is unknown; no paste sent");
                return paragraphStyle == 0 && paragraphMode == 2;
            }

            internal Sequence(NativeUiaKeyboard owner, Target target, Request r)
            {
                this.owner = owner;
                this.target = target;
                owner.ValidateTarget(target, r);
                r.Check();
                // App.typeText follows the current keyboard recipient. Freeze
                // its UIA identity when available; no editor or role is inferred.
                try
                {
                    element = UiaElement.FocusedElement;
                    r.Check();
                }
                catch
                {
                    r.Check();
                    if (target.Node != null)
                        throw;
                }

                if (element != null)
                {
                    runtime = NativeReadStore.Runtime(element);
                    role = NativeUiaBoundary.Property(element, AutomationElement.ControlTypeProperty, r) as ControlType;
                    if (String.IsNullOrEmpty(runtime))
                        throw new Failure("input_unavailable", "the current UIA keyboard recipient has no stable native identity");
                    var recipient = target.Editor ?? target.Node;
                    if (!owner.store.Within(element, target.Window, r) || recipient != null && runtime != recipient.Runtime)
                        throw new Failure("input_unavailable", "UIA focus is outside the selected keyboard recipient");
                }
                else if (target.Node != null)
                    throw new Failure("input_unavailable", "the selected element has no confirmed UIA keyboard focus");
                focus = Native.Focus(target.Window.Native.Handle);
                pid = Native.PID(focus);
                generation = Native.Generation(focus);
                nativeClass = Native.Class(focus);
                multiline = (Native.GetWindowLongPtr(focus, -16).ToInt64() & 4) != 0;
                Validate(r);
            }

            void NativeRecipient(Request r)
            {
                r.Check();
                target.Window.Native.Validate();
                if (!Native.IsWindow(focus) || Native.PID(focus) != pid || Native.Generation(focus) != generation || RichMultiline && (Native.Class(focus) != nativeClass || (Native.GetWindowLongPtr(focus, -16).ToInt64() & 4) == 0) || !Native.Inside(target.Window.Native.Handle, focus) || Native.GetForegroundWindow() != target.Window.Native.Handle || Native.Focus(target.Window.Native.Handle) != focus)
                    throw new Failure("input_unavailable", "the native keyboard recipient changed during continuous input");
            }

            void NativeRoute(Request r)
            {
                NativeRecipient(r);
                if (paragraphStyle.HasValue || paragraphMode.HasValue)
                {
                    if (paragraphStyle.HasValue && (ReadMode(0x4cd, r) & 1) != paragraphStyle.Value || paragraphMode.HasValue && (ReadMode(0x45a, r) & 3) != paragraphMode.Value)
                        throw new Failure("input_unavailable", "the native rich editor paragraph mode changed before paste; no paste sent");
                    NativeRecipient(r); // Message handlers may change focus or destroy their window.
                }
            }

            public void Validate(Request r)
            {
                NativeRoute(r);
                owner.ValidateTarget(target, r);
                if (target.Editor != null)
                    owner.ValidateEditor(target, r);
                if (element != null)
                {
                    r.Check();
                    var current = UiaElement.FocusedElement;
                    r.Check();
                    if (current == null || NativeReadStore.Runtime(current) != runtime || NativeReadStore.Runtime(element) != runtime || NativeUiaBoundary.Property(element, AutomationElement.ControlTypeProperty, r) as ControlType != role || !owner.store.Within(element, target.Window, r) || !NativeKeyboardRoute.Contains(target.Window.Native.Handle, owner.store.NativeHandle(element, r), focus, true))
                        throw new Failure("input_unavailable", "the UIA keyboard recipient changed during continuous input");
                }

                NativeRoute(r); // Provider callbacks cannot leave an unchecked native queue.
            }
        }

        public INativeKeyboardSequence BeginSequence(NativeKeyboardState state, Request r)
        {
            return new Sequence(this, (Target)state.Native, r);
        }

        UiaTextPattern TextPatternFor(NativeKeyboardState state, Request r)
        {
            var target = (Target)state.Native;
            if (target.Node == null)
                throw new Failure("invalid_request", "full replacement needs an observed text control");
            ValidateTarget(target, r);
            var node = target.Editor == null ? store.Node(target.Node.Id, target.Node.Surface, r) : ValidateEditor(target, r);
            object raw;
            r.Check();
            if (!node.Element.TryGetCurrentPattern(TextPattern.Pattern, out raw) || ((UiaTextPattern)raw).SupportedTextSelection == SupportedTextSelection.None)
                throw new Failure("input_unavailable", "the selected control no longer exposes a selectable text range");
            r.Check();
            return (UiaTextPattern)raw;
        }

        public NativeTextSelection Selection(NativeKeyboardState state, Request r)
        {
            var pattern = TextPatternFor(state, r);
            var document = pattern.DocumentRange;
            r.Check();
            var selected = pattern.GetSelection();
            r.Check();
            // Compare native endpoints, never strings, line endings or lengths.
            bool empty = document.CompareEndpoints(TextPatternRangeEndpoint.Start, document, TextPatternRangeEndpoint.End) == 0;
            bool full = selected.Length == 1 && selected[0].CompareEndpoints(TextPatternRangeEndpoint.Start, document, TextPatternRangeEndpoint.Start) == 0 && selected[0].CompareEndpoints(TextPatternRangeEndpoint.End, document, TextPatternRangeEndpoint.End) == 0;
            r.Check();
            return new NativeTextSelection
            {
                Empty = empty,
                Full = full
            };
        }

        public void SelectAll(NativeKeyboardState state, Request r)
        {
            var document = TextPatternFor(state, r).DocumentRange;
            r.Check();
            document.Select();
        }
    }
}
