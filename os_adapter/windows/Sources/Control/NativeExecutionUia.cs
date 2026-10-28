// Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

using System;
using UIA = NativeUIA;
using System.Collections.Generic;
using System.Linq;
using System.Security.Principal;
using System.Threading;
using System.Windows.Automation;

namespace ComputerUse.Windows
{
    internal static class NativeUiaActions
    {
        internal static readonly Dictionary<string, AutomationPattern> Patterns =
            new Dictionary<string, AutomationPattern> {
                { "Invoke.Invoke", InvokePattern.Pattern },
                { "Toggle.Toggle", TogglePattern.Pattern },
                { "SelectionItem.Select", SelectionItemPattern.Pattern },
                { "SelectionItem.AddToSelection", SelectionItemPattern.Pattern },
                { "SelectionItem.RemoveFromSelection", SelectionItemPattern.Pattern },
                { "ExpandCollapse.Expand", ExpandCollapsePattern.Pattern },
                { "ExpandCollapse.Collapse", ExpandCollapsePattern.Pattern },
                { "ScrollItem.ScrollIntoView", ScrollItemPattern.Pattern },
                { "Window.Close", WindowPattern.Pattern }
            };
        internal static string[] Names(HashSet<int> supported)
        {
            return Patterns.Where(p => supported.Contains(p.Value.Id)).Select(p => p.Key).ToArray();
        }
    }

    internal sealed class NativeUiaBoundary
    {
        readonly NativeReadStore store;
        internal NativeUiaBoundary(NativeReadStore store)
        {
            this.store = store;
        }

        internal static object Property(UiaElement element, AutomationProperty property, Request r)
        {
            r.Check();
            var value = element.GetCurrentPropertyValue(property, true);
            r.Check();
            return ReferenceEquals(value, AutomationElement.NotSupported) ? null : value;
        }

        internal static string Label(UiaElement element, Request r)
        {
            try
            {
                return Property(element, AutomationElement.NameProperty, r) as string;
            }
            catch
            {
                r.Check();
                return null;
            } // A label is diagnostic, never input authority.
        }

        static bool OwnedBy(IntPtr candidate, IntPtr owner, Request r)
        {
            var seen = new HashSet<IntPtr>();
            var current = Native.GetWindow(candidate, 4);
            for (int n = 0; n < 64 && current != IntPtr.Zero && seen.Add(current); n++)
            {
                r.Check();
                if (current == owner)
                    return true;
                current = Native.GetWindow(current, 4);
            }

            return false;
        }

        internal static void SameWindow(NativeWindowRecord expected, NativeWindowRecord current, Request r)
        {
            expected.Native.Validate();
            current.Native.Validate();
            r.Check();
            if (!ReferenceEquals(expected, current))
                throw new Failure(
                    "stale_target",
                    "the selected element's native interaction window changed during input preparation; no remaining input was sent");
        }

        NativeWindowRecord Modal(IntPtr handle, Request r)
        {
            var window = store.RetainWindow(handle, r);
            object modal = null;
            try
            {
                modal = Property(UiaElement.FromHandle(handle), WindowPattern.IsModalProperty, r);
            }
            catch (Exception)
            {
                r.Check();
            } // Unknown modality is not a confirmed modal.

            if (!(modal is bool) || !(bool)modal)
                return null;
            window.Native.Validate();
            return window;
        }

        static void RequireInteraction(NativeWindowRecord observed, NativeWindowRecord interaction, Request r)
        {
            observed.Native.Validate();
            interaction.Native.Validate();
            r.Check();
            if (!ReferenceEquals(observed, interaction) &&
                (!OwnedBy(interaction.Native.Handle, observed.Native.Handle, r) ||
                 !Native.IsWindowVisible(interaction.Native.Handle) || Native.IsIconic(interaction.Native.Handle)))
                throw new Failure("stale_target", "the selected element's owned modal interaction window changed");
            if (!Native.IsWindowEnabled(interaction.Native.Handle))
                throw new Failure("input_unavailable",
                                  "the selected native interaction window is disabled; the reason is not inferred");
        }

        NativeWindowRecord WindowBoundary(NativeWindowRecord window, NativeNodeRecord node, Request r)
        {
            window.Native.Validate();
            r.Check();
            var interaction = window;
            var modals = new List<NativeWindowRecord>();
            if (node != null)
            {
                IntPtr host = store.NativeHandle(node.Element, r),
                       top = host == IntPtr.Zero ? IntPtr.Zero : Native.GetAncestor(host, 2);
                if (top != IntPtr.Zero && top != window.Native.Handle && !OwnedBy(top, window.Native.Handle, r) &&
                    Modal(top, r) != null)
                    throw new Failure(
                        "stale_target",
                        "the selected control's native modal host is outside its original working window");
            }

            foreach (var candidate in Native.Windows())
            {
                if (candidate == window.Native.Handle || !OwnedBy(candidate, window.Native.Handle, r))
                    continue;
                // Capture HWND lifetime before provider callbacks. A modal's
                // observation surface need not be its descendant's input host.
                var native = Modal(candidate, r);
                if (native == null)
                    continue;
                if (node == null || !store.Within(node.Element, native, r))
                    throw new Failure("blocked_by_modal", "an owned native modal window blocks the selected control");
                // All blocking modals must contain this exact live element and
                // form one native owner chain. PID or tree order is not proof.
                if (OwnedBy(candidate, interaction.Native.Handle, r))
                    interaction = native;
                else if (!OwnedBy(interaction.Native.Handle, candidate, r))
                    throw new Failure("blocked_by_modal",
                                      "the selected control has no unique owned modal interaction window");
                modals.Add(native);
            }

            window.Native.Validate();
            r.Check();
            if (node != null && !store.Within(node.Element, interaction, r))
                throw new Failure("stale_target", "the selected control left its native interaction window");
            foreach (var modal in modals)
            {
                modal.Native.Validate();
                if (!OwnedBy(modal.Native.Handle, window.Native.Handle, r) ||
                    !Native.IsWindowVisible(modal.Native.Handle))
                    throw new Failure("stale_target", "the owned modal window changed during input validation");
            }

            RequireInteraction(window, interaction, r);
            return interaction;
        }
        // QUERY-only explicit caller scope on the original retained node.
        // Each invocation owns fresh traversal state; it grants no durable input permission.
        internal void RequireWithin(NativeNodeRecord node, string[] within, NativeWindowRecord window, Request r)
        {

            if (within.Contains(node.Id))
                throw new Failure("invalid_request", "ancestor constraints must not include the selected control");
            var requiredAncestors = new HashSet<string>(within.Select(id => store.Node(id, node.Surface, r).Runtime));
            if (requiredAncestors.Count != 0)
            {
                var current = UiaClient.Parent(node.Element);
                var visited = new HashSet<string>();
                for (int n = 0; n < 128 && current != null; n++)
                {
                    r.Check();
                    string key = NativeReadStore.Runtime(current);
                    if (!visited.Add(key))
                        break;
                    requiredAncestors.Remove(key);
                    if (requiredAncestors.Count == 0)
                        break;
                    if (!store.Within(current, window, r))
                        break;
                    current = UiaClient.Parent(current);
                }

                if (requiredAncestors.Count != 0)
                    throw new Failure("stale_target", "the control left its observed ancestor scope");
            }
        }
        internal NativeWindowRecord Validate(Dictionary<string, object> target, string[] within, Request r)
        {
            r.Check();
            if (!Native.Interactive())
                throw new Failure("desktop_unavailable",
                                  "native input requires the current unlocked interactive desktop");
            var identity = store.ValidateInputTarget(target, r);
            var window = identity.Window;
            if (window == null)
                throw new Failure("invalid_request", "native input needs an observed working window");
            if (target["node"] == null)
            {
                if (within.Length != 0)
                    throw new Failure("invalid_request", "window input cannot have element ancestors");
                return WindowBoundary(window, null, r);
            }
            // ValidateInputTarget just checked this original Node; no provider
            // callback occurred since. The final store.Validate below remains
            // mandatory after ancestor/modal queries and is not memoized.
            var node = identity.Node;
            RequireWithin(node, within, window, r);

            // UIA callbacks during parent queries may destroy or move windows.
            var interaction = WindowBoundary(window, node, r);
            store.Validate(target, r);
            // Modal and final original-identity provider queries can reparent the
            // same live node. Recheck only explicit constraints, never the whole boundary.
            if (within.Length != 0)
                RequireWithin(node, within, window, r);
            RequireInteraction(window, interaction, r);
            r.Check();
            return interaction;
        }
    }
    internal sealed partial class NativeUiaObjects : INativeObjectProvider,
                                                     INativeClickObjectProvider,
                                                     INativeExpansionObjectProvider
    {

        readonly NativeReadStore store;
        readonly NativeUiaBoundary boundary;
        internal NativeUiaObjects(NativeReadStore store)
        {
            this.store = store;
            boundary = new NativeUiaBoundary(store);
        }

        sealed class Lease : IDisposable
        {
            Mutex mutex;
            bool held;
            internal Lease(Request r)
            {
                mutex = new Mutex(false, "Local\\ComputerUse-" + WindowsIdentity.GetCurrent().User.Value);
                try
                {
                    while (!held)
                    {
                        r.Check();
                        try
                        {
                            held = mutex.WaitOne(25);
                        }
                        catch (AbandonedMutexException)
                        {
                            held = true;
                        }
                    }

                    r.Check();
                }
                catch
                {
                    Dispose();
                    throw;
                }
            }

            public void Dispose()
            {
                if (mutex == null)
                    return;
                var current = mutex;
                if (held)
                {
                    current.ReleaseMutex();
                    held = false;
                }

                current.Dispose();
                mutex = null;
            }
        }

        public IDisposable Acquire(Request r)
        {
            return new Lease(r);
        }

        public NativeObjectState Read(Dictionary<string, object> target, string[] within, Request r)
        {
            return ReadState(target, within, r, false);
        }

        public NativeObjectState ReadClick(Dictionary<string, object> target, string[] within, Request r)
        {
            return ReadState(target, within, r, true);
        }
        public NativeObjectState ReadExpansion(Dictionary<string, object> target, string[] within, Request r)
        {
            return ReadState(target, within, r, true, true);
        }
        NativeObjectState ReadState(Dictionary<string, object> target, string[] within, Request r, bool click,
                                    bool expansion = false)
        {

            // Resolve the retained object first; provider calls may change the
            // UI, so scope/ancestors/modal are checked once after those calls.
            // Repeated global scans cannot make independent UIA calls atomic.
            var window = store.Validate(target, r);
            if (window == null || target["node"] == null)
                throw new Failure("invalid_request", "object input needs an observed native element");
            var node = store.Node((string)target["node"], (string)target["surface"], r);
            var element = node.Element;
            var state = new NativeObjectState { Native = element };
            Action metadata = () =>
            {
                var role = NativeUiaBoundary.Property(element, AutomationElement.ControlTypeProperty, r) as ControlType;
                state.Role = role == null ? null : role.ProgrammaticName;
                state.Label = NativeUiaBoundary.Label(element, r);
                state.Enabled = NativeUiaBoundary.Property(element, AutomationElement.IsEnabledProperty, r) as bool ? ;
                r.Check();
                var patterns = new HashSet<int>(element.GetSupportedPatterns().Select(p => p.Id));
                r.Check();
                state.Actions = NativeUiaActions.Names(patterns);
                state.ActionsComplete = true;
                if (expansion && patterns.Contains(ExpandCollapsePattern.Pattern.Id))
                {
                    var observed =
                        NativeUiaBoundary.Property(element, ExpandCollapsePattern.ExpandCollapseStateProperty, r);
                    state.CollapsedExpansion =
                        observed is ExpandCollapseState
                            ? (bool?)((ExpandCollapseState)observed == ExpandCollapseState.Collapsed)
                            : null;
                }
                state.Replaceable = false;
                if (!click && patterns.Contains(ValuePattern.Pattern.Id))
                {
                    NativeObjectValueObservation.Read(
                        state, () => NativeUiaBoundary.Property(element, ValuePattern.IsReadOnlyProperty, r),
                        () => NativeUiaBoundary.Property(element, AutomationElement.IsPasswordProperty, r),
                        () => NativeUiaBoundary.Property(element, ValuePattern.ValueProperty, r), r);
                }
            };
            if (click)
                NativeClickObjectObservation.Read(state, metadata, () => boundary.Validate(target, within, r), r);
            else
            {
                metadata();
                boundary.Validate(target, within, r);
            }

            return state;
        }

        static Action WithRepaint(Action mutate)
        {
            return () =>
            {
                try
                {
                    mutate();
                }
                finally
                {
                    Native.NoteVisualInput();
                }
            };
        }

        public Action Prepare(NativeObjectState state, string unit, string action, string text, Request r)
        {
            var element = (UiaElement)state.Native;
            object pattern;
            r.Check();
            if (unit == "native_text_edit")
            {
                if (!element.TryGetCurrentPattern(ValuePattern.Pattern, out pattern))
                    throw new Failure("input_unavailable", "the addressed ValuePattern is no longer available");
                var value = (UIA.IUIAutomationValuePattern)pattern;
                if (value.CurrentIsReadOnly != 0)
                    throw new Failure("input_unavailable", "the addressed value became read-only");
                r.Check();
                return WithRepaint(() => value.SetValue(text));
            }

            AutomationPattern type;
            if (!NativeUiaActions.Patterns.TryGetValue(action, out type) ||
                !element.TryGetCurrentPattern(type, out pattern))
                throw new Failure("input_unavailable", "the selected UIA pattern is no longer available");
            r.Check();
            switch (action)
            {
            case "Invoke.Invoke":
                return WithRepaint(() => ((UIA.IUIAutomationInvokePattern)pattern).Invoke());
            case "Toggle.Toggle":
                return WithRepaint(() => ((UIA.IUIAutomationTogglePattern)pattern).Toggle());
            case "SelectionItem.Select":
                return WithRepaint(() => ((UIA.IUIAutomationSelectionItemPattern)pattern).Select());
            case "SelectionItem.AddToSelection":
                return WithRepaint(() => ((UIA.IUIAutomationSelectionItemPattern)pattern).AddToSelection());
            case "SelectionItem.RemoveFromSelection":
                return WithRepaint(() => ((UIA.IUIAutomationSelectionItemPattern)pattern).RemoveFromSelection());
            case "ExpandCollapse.Expand":
                return WithRepaint(() => ((UIA.IUIAutomationExpandCollapsePattern)pattern).Expand());
            case "ExpandCollapse.Collapse":
                return WithRepaint(() => ((UIA.IUIAutomationExpandCollapsePattern)pattern).Collapse());
            case "ScrollItem.ScrollIntoView":
                return WithRepaint(() => ((UIA.IUIAutomationScrollItemPattern)pattern).ScrollIntoView());
            case "Window.Close":
                return WithRepaint(() => ((UIA.IUIAutomationWindowPattern)pattern).Close());
            default:
                throw new Failure("invalid_request", "unknown native UIA action");
            }
        }
    }
}
