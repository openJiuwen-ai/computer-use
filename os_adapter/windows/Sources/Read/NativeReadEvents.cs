using System;
using System.Collections.Generic;
using System.Linq;
using System.Runtime.InteropServices;
using System.Threading;
using System.Windows.Automation;
using UIA = NativeUIA;

namespace ComputerUse.Windows
{
    // Registration/removal runs on the driver's long-lived windowless MTA.
    // Callbacks never query a provider, retain senders, or publish UI facts.
    internal sealed class NativeReadEvents : IDisposable
    {
        [ComVisible(true), ClassInterface(ClassInterfaceType.None)]
        internal sealed class Listener : UIA.IUIAutomationEventHandler, UIA.IUIAutomationPropertyChangedEventHandler, UIA.IUIAutomationStructureChangedEventHandler
        {
            readonly object gate = new object ();
            readonly string epoch = Guid.NewGuid().ToString("N");
            long sequence;
            readonly Dictionary<string, long> changes = new Dictionary<string, long>();
            bool active = true;
            internal string Token
            {
                get
                {
                    lock (gate)
                        return epoch + ":" + sequence;
                }
            }

            internal bool Owns(string token)
            {
                long version;
                lock (gate)
                    return token.StartsWith(epoch + ":", StringComparison.Ordinal) && Int64.TryParse(token.Substring(epoch.Length + 1), out version) && version >= 0 && version <= sequence;
            }

            internal void Stop()
            {
                lock (gate)
                {
                    active = false;
                    Monitor.PulseAll(gate);
                }
            }

            void Changed(string kind)
            {
                lock (gate)
                {
                    if (!active)
                        return;
                    changes[kind] = ++sequence;
                    Monitor.PulseAll(gate);
                }
            }

            internal string[] KindsSince(string token)
            {
                lock (gate)
                {
                    long version = Int64.Parse(token.Substring(epoch.Length + 1));
                    return changes.Where(p => p.Value > version).Select(p => p.Key).ToArray();
                }
            }

            internal bool ChangedSince(string token)
            {
                lock (gate)
                    return active && token != epoch + ":" + sequence;
            }

            internal void Pause(int milliseconds)
            {
                lock (gate)
                {
                    if (active)
                        Monitor.Wait(gate, milliseconds);
                }
            }

            public void HandleAutomationEvent(UIA.IUIAutomationElement sender, int eventId)
            {
                Changed(eventId == 20014 ? "selection" : "text");
            }

            public void HandlePropertyChangedEvent(UIA.IUIAutomationElement sender, int propertyId, object value)
            {
                Changed(propertyId == 30008 ? "focus" : "property");
            }

            public void HandleStructureChangedEvent(UIA.IUIAutomationElement sender, UIA.StructureChangeType kind, Array runtimeId)
            {
                Changed("structure");
            }
        }

        Listener listener;
        NativeWindowRecord window;
        UIA.IUIAutomationElement root;
        UIA.IUIAutomation client;
        bool structure, properties, text, selection;
        string failedWindow;
        internal bool NeedsRestart { get; private set; }

        internal string Observe(NativeWindowRecord selected)
        {
            selected.Native.Validate();
            if (window != null && window.Native.Ref == selected.Native.Ref)
                return listener.Token;
            Dispose();
            if (failedWindow == selected.Native.Ref)
                return null;
            window = selected;
            listener = new Listener();
            client = UiaClient.Current;
            if (NeedsRestart)
            {
                Dispose();
                return null;
            }

            try
            {
                using (UiaClient.TransactionBudget(300))
                {
                    root = UiaElement.FromHandle(selected.Native.Handle).Native;
                    client.AddStructureChangedEventHandler(root, (UIA.TreeScope)5, null, listener);
                    structure = true;
                    // HasKeyboardFocus changes are scoped. Global focus
                    // callbacks cannot be restricted to the selected window.
                    client.AddPropertyChangedEventHandler(root, (UIA.TreeScope)5, null, listener, new int[] { AutomationElement.HasKeyboardFocusProperty.Id, AutomationElement.NameProperty.Id, AutomationElement.IsEnabledProperty.Id, AutomationElement.IsOffscreenProperty.Id, ValuePattern.ValueProperty.Id, TogglePattern.ToggleStateProperty.Id, ExpandCollapsePattern.ExpandCollapseStateProperty.Id, SelectionItemPattern.IsSelectedProperty.Id });
                    properties = true;
                    client.AddAutomationEventHandler(20015, root, (UIA.TreeScope)5, null, listener);
                    text = true;
                    client.AddAutomationEventHandler(20014, root, (UIA.TreeScope)5, null, listener);
                    selection = true;
                }

                selected.Native.Validate();
                return listener.Token;
            }
            catch
            {
                failedWindow = selected.Native.Ref;
                Dispose();
                return null;
            }
        }

        internal object Wait(string session, NativeWindowRecord selected, string after, int milliseconds, Request request)
        {
            double start = Wire.Now, end = Math.Min(request.Deadline, start + milliseconds);
            string reason = "unavailable";
            if (window != null && window.Native.Ref == selected.Native.Ref && listener != null && listener.Owns(after))
            {
                reason = "timeout";
                while (true)
                {
                    request.Check();
                    selected.Native.Validate();
                    if (listener.ChangedSince(after))
                    {
                        reason = "event";
                        break;
                    }

                    int left = (int)Math.Ceiling(end - Wire.Now);
                    if (left <= 0)
                        break;
                    listener.Pause(Math.Min(20, left));
                }
            }

            request.Check();
            selected.Native.Validate();
            var kinds = reason == "unavailable" ? new string[0] : listener.KindsSince(after);
            if (reason != "unavailable")
                reason = kinds.Length == 0 ? "timeout" : "event";
            return Wire.Obj("kind", "readChange", "session", session, "surface", selected.Native.Scope, "after", after, "reason", reason, "events", kinds, "elapsedMs", Math.Max(0, (int)(Wire.Now - start)));
        }

        public void Dispose()
        {
            if (listener == null)
                return;
            // Disable first; COM may deliver a late callback after removal.
            // Its CCW retains the managed object for as long as COM needs it.
            var old = listener;
            old.Stop();
            try
            {
                using (UiaClient.TransactionBudget(300))
                {
                    if (text)
                        try
                        {
                            client.RemoveAutomationEventHandler(20015, root, old);
                        }
                        catch
                        {
                            NeedsRestart = true;
                        }

                    if (selection)
                        try
                        {
                            client.RemoveAutomationEventHandler(20014, root, old);
                        }
                        catch
                        {
                            NeedsRestart = true;
                        }

                    if (properties)
                        try
                        {
                            client.RemovePropertyChangedEventHandler(root, old);
                        }
                        catch
                        {
                            NeedsRestart = true;
                        }

                    if (structure)
                        try
                        {
                            client.RemoveStructureChangedEventHandler(root, old);
                        }
                        catch
                        {
                            NeedsRestart = true;
                        }
                }
            }
            catch
            {
                NeedsRestart = true;
            }
            finally
            {
                listener = null;
                root = null;
                window = null;
                client = null;
                structure = properties = text = selection = false;
            }
        }
    }
}
