// Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

using System;
using System.Collections.Generic;
using System.Windows.Automation;
using UIA = NativeUIA;

namespace ComputerUse.Windows
{
    internal sealed partial class NativeUiaObjects
    {
        NativeSelectionInsertOffer OfferMultilineReplacement(Dictionary<string, object> target, string[] within,
                                                             NativeNodeRecord node, NativeWindowRecord window,
                                                             bool hasWritableValue, Request r)
        {
            var element = node.Element;
            var raw = NativeObjectValueObservation.Property(
                () => NativeUiaBoundary.Property(element, AutomationElement.NativeWindowHandleProperty, r), r);
            if (!(raw is int) || (int)raw == 0 || String.IsNullOrEmpty(node.Runtime))
                return null;
            var hwnd = new IntPtr((int)raw);
            uint pid;
            uint thread = Native.GetWindowThreadProcessId(hwnd, out pid);
            if (thread == 0 || pid != window.Native.Pid || hwnd == window.Native.Handle ||
                !Native.Inside(window.Native.Handle, hwnd) || !Native.GenerationTrackingAvailable)
                return null;
            long generation = Native.Generation(hwnd);
            var classes = NativeCharacterWin32.ClassEvidence(hwnd);
            if (!classes.Unicode || NativeSelectionInsertMechanism.NativeFramework(classes.NativeClass, classes.RealClass) == null)
                return null;
            long? style = NativeSelectionInsertWin32.ObserveStyle(hwnd);
            if (!style.HasValue || (style.Value & 4) == 0)
                return null;
            // Standard multiline Edit sends EN_CHANGE for EM_REPLACESEL.
            // The WinForms event canary saw WM_SETTEXT change the value without
            // EN_CHANGE or enabling Submit. Virtual elements retain their route.
            var sequence = NativeObjectValueObservation.Property(() =>
            {
                var framework = NativeUiaBoundary.Property(element, AutomationElement.FrameworkIdProperty, r) as string;
                var uiaClass = NativeUiaBoundary.Property(element, AutomationElement.ClassNameProperty, r) as string;
                var mechanism = NativeSelectionInsertMechanism.ResolveReplacement(classes.NativeClass, classes.RealClass,
                                                                                    framework, classes.Unicode, style.Value, uiaClass);
                if (mechanism == null ||
                    !HasReplacementRole(element, r) ||
                    NativeUiaBoundary.Property(element, AutomationElement.IsPasswordProperty, r) as bool? != false ||
                    !hasWritableValue && !HasWritableTextRange(element, r))
                    return null;
                var candidate = new MultilineReplacement(boundary, target, within, node, window, hwnd, thread, generation,
                                                         mechanism, uiaClass);
                candidate.ValidateLifetime(r);
                return MatchesNativeEdit(element, node.Runtime, hwnd, r) ? candidate : null;
            }, r) as MultilineReplacement;
            return sequence == null ? null : new NativeSelectionInsertOffer(sequence,
                new NativeSelectionInsertState { Role = "ControlType.Edit" });
        }

        // The same WinForms HWND can report Edit/Value before focus and
        // Document/Text after focus. The native class and identity own the route.
        static bool HasReplacementRole(UiaElement element, Request r)
        {
            return NativeSelectionInsertMechanism.IsReplacementRole(
                NativeUiaBoundary.Property(element, AutomationElement.ControlTypeProperty, r) as ControlType);
        }

        static bool HasWritableTextRange(UiaElement element, Request r)
        {
            return NativeUiaKeyboard.ReadOnly(element, TextPattern.Pattern, r) == false;
        }

        static bool MatchesNativeEdit(UiaElement element, string runtime, IntPtr hwnd, Request r)
        {
            r.Check();
            var native = UiaElement.FromHandle(hwnd);
            bool same = native != null && NativeReadStore.Runtime(native) == runtime &&
                        UiaClient.Call(() => UiaClient.Current.CompareElements(element.Native, native.Native)) != 0;
            r.Check();
            return same;
        }

        sealed class MultilineReplacement : INativeSelectionReplacementSequence
        {
            readonly NativeUiaBoundary boundary;
            readonly Dictionary<string, object> target;
            readonly string[] within;
            readonly UiaElement element;
            readonly string runtime;
            readonly NativeWindowRecord window;
            readonly IntPtr hwnd;
            readonly uint thread;
            readonly long generation;
            readonly NativeSelectionInsertMechanism mechanism;
            readonly string uiaClass;

            internal MultilineReplacement(NativeUiaBoundary boundary, Dictionary<string, object> target, string[] within,
                                          NativeNodeRecord node, NativeWindowRecord window, IntPtr hwnd, uint thread,
                                          long generation, NativeSelectionInsertMechanism mechanism, string uiaClass)
            {
                this.boundary = boundary;
                this.target = new Dictionary<string, object>(target);
                this.within = (string[])within.Clone();
                this.element = node.Element;
                this.runtime = node.Runtime;
                this.window = window;
                this.hwnd = hwnd;
                this.thread = thread;
                this.generation = generation;
                this.mechanism = mechanism;
                this.uiaClass = uiaClass;
            }

            public void ValidateSelectionTarget(Request r)
            {
                object value;
                if (!HasReplacementRole(element, r) ||
                    NativeUiaBoundary.Property(element, AutomationElement.NativeWindowHandleProperty, r) as int? != hwnd.ToInt32() ||
                    NativeUiaBoundary.Property(element, AutomationElement.IsEnabledProperty, r) as bool? != true ||
                    NativeUiaBoundary.Property(element, AutomationElement.IsPasswordProperty, r) as bool? != false)
                    throw new Failure("input_unavailable", "the original multiline Edit is no longer confirmed writable");
                bool writable = element.TryGetCurrentPattern(ValuePattern.Pattern, out value)
                                    ? ((UIA.IUIAutomationValuePattern)value).CurrentIsReadOnly == 0
                                    : HasWritableTextRange(element, r);
                if (!writable)
                    throw new Failure("input_unavailable", "the original multiline Edit is no longer confirmed writable");
                ValidateLifetime(r);
                if (!MatchesNativeEdit(element, runtime, hwnd, r))
                    throw new Failure("stale_target", "the original multiline Edit no longer matches its native element");
                NativeUiaBoundary.SameWindow(window, boundary.Validate(target, within, r), r);
                NativeInputAccess.RequireWindow(hwnd);
                ValidateNative(r);
            }

            internal void ValidateLifetime(Request r)
            {
                window.Native.Validate();
                uint pid;
                if (!Native.GenerationTrackingAvailable || !Native.IsWindow(hwnd) ||
                    Native.GetWindowThreadProcessId(hwnd, out pid) != thread || pid != window.Native.Pid ||
                    Native.Generation(hwnd) != generation || !Native.Inside(window.Native.Handle, hwnd))
                    throw new Failure("stale_target", "the original multiline Edit HWND or lifetime changed");
                r.Check();
            }

            void ValidateNative(Request r)
            {
                ValidateLifetime(r);
                var classes = NativeCharacterWin32.ClassEvidence(hwnd);
                long style = NativeSelectionInsertWin32.Style(hwnd);
                if (!Native.IsWindowEnabled(hwnd) || !Native.IsWindowEnabled(window.Native.Handle) ||
                    !mechanism.Same(NativeSelectionInsertMechanism.ResolveReplacement(classes.NativeClass, classes.RealClass,
                                                                                      mechanism.Framework, classes.Unicode, style, uiaClass)))
                    throw new Failure("input_unavailable", "the original multiline Edit is disabled or its native mechanism changed");
                r.Check();
            }

            NativeSelectionInsertState ReadSelection(Request r)
            {
                var length = NativeSelectionInsertWin32.TextLength(hwnd, r);
                // These queries enter the target window procedure. Stop before
                // the second query if the first ended the retained lifetime.
                ValidateLifetime(r);
                return new NativeSelectionInsertState { Role = "ControlType.Edit", Enabled = true, Writable = true,
                    Eligible = true, Ready = true, TextLength = length,
                    Selection = NativeSelectionInsertWin32.Selection(hwnd, r) };
            }

            public NativeSelectionInsertState Read(Request r)
            {
                ValidateNative(r);
                return ReadSelection(r);
            }

            public void ValidateFinal(NativeSelectionInsertState state, Request r)
            {
                ValidateSelectionTarget(r);
                if (!NativeSelectionInsertPipeline.CoversText(ReadSelection(r)))
                    throw new Failure("input_unavailable", "the original edit no longer has the complete selection");
                if (!Native.Interactive())
                    throw new Failure("desktop_unavailable", "the original input desktop changed during selection validation");
                ValidateNative(r);
            }

            public IDisposable CreateBuffer(string text) { return NativeSelectionInsertWin32.CreateBuffer(text); }
            public NativeSelectionInsertSendResult SelectAll(int timeoutMs)
            { return NativeSelectionInsertWin32.SelectAll(hwnd, timeoutMs); }
            public NativeSelectionInsertSendResult Send(IDisposable buffer, int timeoutMs)
            { return NativeSelectionInsertWin32.Send(hwnd, buffer, timeoutMs); }
        }
    }
}
