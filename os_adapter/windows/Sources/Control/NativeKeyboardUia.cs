using System;
using System.Collections.Generic;
using System.Linq;
using System.Runtime.InteropServices;
using System.Windows.Automation;

namespace ComputerUse.Windows
{
    internal sealed partial class NativeUiaKeyboard : INativeKeyboardProvider, INativeKeyboardTextProvider, INativeKeyboardEditorPreparation
    {
        readonly NativeReadStore store;
        readonly NativeUiaBoundary boundary;
        readonly NativePointer pointer;
        readonly string mode;
        sealed class Target
        {
            internal NativeWindowRecord Window;
            internal NativeNodeRecord Node, Editor;
            internal Dictionary<string, object> Binding;
            internal string[] Within;
        }

        internal NativeUiaKeyboard(NativeReadStore store, NativePointer pointer = null, string mode = "shared")
        {
            this.store = store;
            this.pointer = pointer;
            this.mode = mode;
            boundary = new NativeUiaBoundary(store);
        }

        void ValidateTarget(Target target, Request r)
        {
            NativeUiaBoundary.SameWindow(target.Window, boundary.Validate(target.Binding, target.Within, r), r);
        }

        public NativeKeyboardState Read(Dictionary<string, object> target, string[] within, Request r, bool text = false)
        {
            var window = store.Validate(target, r);
            if (window == null)
                throw new Failure("invalid_request", "keyboard input needs an observed working window");
            var node = target["node"] == null ? null : store.Node((string)target["node"], (string)target["surface"], r);
            var state = new NativeKeyboardState
            {
                Native = new Target
                {
                    Window = window,
                    Node = node,
                    Binding = new Dictionary<string, object>(target),
                    Within = (string[])within.Clone()
                },
                Enabled = true,
                Focused = node == null,
                Label = window.Native.Name
            };
            if (node != null)
            {
                var role = NativeUiaBoundary.Property(node.Element, AutomationElement.ControlTypeProperty, r) as ControlType;
                state.Role = role == null ? null : role.ProgrammaticName;
                state.Label = NativeUiaBoundary.Label(node.Element, r);
                state.Enabled = NativeUiaBoundary.Property(node.Element, AutomationElement.IsEnabledProperty, r) as bool? ;
                state.Focusable = NativeUiaBoundary.Property(node.Element, AutomationElement.IsKeyboardFocusableProperty, r) as bool? ;
                if (text || state.Role == "ControlType.ComboBox")
                    ReadTextCapability(node.Element, state, r);
            }

            window = boundary.Validate(target, within, r);
            ((Target)state.Native).Window = window;
            UiaElement focused = null;
            try
            {
                r.Check();
                focused = UiaElement.FocusedElement;
                r.Check();
            }
            catch
            {
                r.Check();
            }

            var receiver = node;
            if (node != null)
            {
                // Only a declared writable composite can own an implicit editor.
                // Its focused leaf must have live ancestry to this exact owner.
                if (state.Role == "ControlType.ComboBox" && state.TextWritable == true && !state.TextSelection)
                {
                    state.SelectionPreparable = true;
                    var editor = ResolveEditor(node, focused, r);
                    if (editor != null)
                    {
                        ((Target)state.Native).Editor = receiver = editor;
                        state.EditorNode = editor.Id;
                        state.EditorOwner = node.Id;
                        var role = NativeUiaBoundary.Property(editor.Element, AutomationElement.ControlTypeProperty, r) as ControlType;
                        state.EditorRole = role == null ? null : role.ProgrammaticName;
                        ReadTextCapability(editor.Element, state, r);
                        state.Enabled = state.Enabled == true ? NativeUiaBoundary.Property(editor.Element, AutomationElement.IsEnabledProperty, r) as bool? : state.Enabled;
                        state.Focusable = NativeUiaBoundary.Property(editor.Element, AutomationElement.IsKeyboardFocusableProperty, r) as bool? ;
                    }
                }

                state.Focused = focused != null && NativeReadStore.Runtime(focused) == receiver.Runtime && !(state.SelectionPreparable && receiver == node);
                if (state.Focused)
                    state.FocusedNode = receiver.Id;
            }

            var handle = window.Native.Handle;
            var inputOwner = receiver == null ? handle : store.NativeHandle(receiver.Element, r);
            // UIA callbacks may run arbitrary application code. Recheck retained
            // identity after them, then sample the native queue immediately last.
            ValidateTarget((Target)state.Native, r);
            if (receiver != null && receiver != node)
                ValidateEditor((Target)state.Native, r);
            state.Foreground = Native.GetForegroundWindow() == handle;
            var nativeFocus = Native.Focus(handle);
            state.WindowRoute = state.Foreground && NativeKeyboardRoute.Contains(handle, inputOwner, nativeFocus, node != null && state.Focused);
            if (state.WindowRoute)
                NativeInputAccess.RequireWindow(handle);
            r.Check();
            return state;
        }

        public void RequireIdle(NativeKeyEvent[] events, Request r)
        {
            r.Check();
            NativeDesktopState.RequireInput();
            Native.Released();
            foreach (ushort code in events.Where(e => !e.Up && !e.Unicode).Select(e => e.Code).Distinct())
                if ((Native.GetAsyncKeyState(code) & 0x8000) != 0)
                    throw new Failure("input_unavailable", "a requested key is already held; no input sent");
            r.Check();
        }

        public void Activate(NativeKeyboardState state, Request r)
        {
            var target = (Target)state.Native;
            ValidateTarget(target, r);
            RequireIdle(new NativeKeyEvent[0], r);
            // This unit is reached only after Python and NativeKeyboard have
            // authorized foreground preparation, before requested input. Use
            // the existing bounded Win32/UIA window activation helper.
            Native.Foreground(target.Window.Native, true, r, mode == "exclusive", () => ValidateTarget(target, r));
            ValidateTarget(target, r);
        }

        public void Focus(NativeKeyboardState state, Request r)
        {
            var target = (Target)state.Native;
            ValidateTarget(target, r);
            if (target.Node == null)
                throw new Failure("invalid_request", "UIA focus requires a selected element");
            var node = target.Editor == null ? store.Node(target.Node.Id, target.Node.Surface, r) : ValidateEditor(target, r);
            if (NativeUiaBoundary.Property(node.Element, AutomationElement.IsKeyboardFocusableProperty, r) as bool? != true)
                throw new Failure("input_unavailable", "the original control no longer supports keyboard focus");
            RequireIdle(new NativeKeyEvent[0], r);
            ValidateTarget(target, r);
            // SetFocus providers can activate a window. Never call it to probe
            // an inactive window under strict shared policy.
            if (Native.GetForegroundWindow() != target.Window.Native.Handle)
                throw new Failure("input_unavailable", "the selected window is no longer foreground; no focus request sent");
            r.Check();
            try
            {
                node.Element.SetFocus();
            }
            finally
            {
                Native.NoteVisualInput();
            }
        }

        public int Send(NativeKeyEvent[] events)
        {
            var inputs = events.Select(e => Native.Key(e.Code, e.Up, e.Unicode, e.Extended)).ToArray();
            return (int)Native.SendInput((uint)inputs.Length, inputs, Marshal.SizeOf(typeof(Native.INPUT)));
        }

        public void PrepareEditor(NativeKeyboardState state, NativeWriteOperation operation, Request r, List<string> preparation)
        {
            var target = (Target)state.Native;
            if (pointer == null || target.Node == null || state.Role != "ControlType.ComboBox" || !state.SelectionPreparable)
                throw new Failure("input_unavailable", "the selected control has no supported internal-editor preparation");
            ValidateTarget(target, r);
            RequireIdle(new NativeKeyEvent[0], r);
            if (Native.GetForegroundWindow() != target.Window.Native.Handle)
                throw new Failure("input_unavailable", "the selected window lost foreground before editor preparation; no click sent");
            var click = new NativeWriteOperation(target.Binding, Wire.Obj("kind", "click", "parameters", Wire.Obj()))
            {
                DisplayPointer = operation.DisplayPointer
            };
            click.Bind(target.Binding, target.Within);
            var facts = Wire.Object(pointer.Inspect(click, target.Binding, target.Within, r));
            var primitive = Wire.Object(Wire.Array(facts["primitives"])[0]);
            preparation.Add("editorFocusClick");
            var result = Wire.Object(pointer.Execute(click, Wire.Obj("kind", "pointer_sequence", "primitiveId", primitive["id"], "target", target.Binding, "foreground", false), r));
            if (click.Cleanup != "complete")
                operation.Cleanup = click.Cleanup;
            if (result["errorCode"] != null)
                throw new Failure(click.Dispatched ? "execution_unknown" : (string)result["errorCode"], "Internal-editor focus preparation: " + (string)result["detail"] + " No text was sent.");
        }
    }
}
