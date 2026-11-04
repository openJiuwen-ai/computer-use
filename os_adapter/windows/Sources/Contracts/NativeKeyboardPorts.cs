// Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

using System;
using System.Collections.Generic;
using System.Linq;

namespace ComputerUse.Windows
{
    internal struct NativeKeyEvent
    {
        internal ushort Code;
        internal bool Up, Extended, Unicode;
        internal NativeKeyEvent(ushort code, bool up, bool unicode = false)
        {
            Code = code;
            Up = up;
            Unicode = unicode;
            Extended = !unicode && (code >= 33 && code <= 46 || code == 91);
        }
    }

    internal sealed class NativeKeyboardState
    {
        internal object Native;
        internal string Role, Label, EditorRole, FocusedNode, EditorNode, EditorOwner;
        internal bool? Enabled, Focusable, TextWritable;
        internal bool TextSelection, SelectionPreparable;
        internal bool Foreground, WindowRoute, Focused;
        internal bool Ready
        {
            get {
                return Enabled == true && Foreground && WindowRoute && Focused;
            }
        }
    }

    internal interface INativeKeyboardProvider
    {
        NativeKeyboardState Read(Dictionary<string, object> target, string[] within, Request request,
                                 bool text = false);
        void RequireIdle(NativeKeyEvent[] events, Request request);
        void Activate(NativeKeyboardState state, Request request);
        void Focus(NativeKeyboardState state, Request request);
        // No request/deadline argument: paired releases must survive cancellation
        // and target destruction. Return the native accepted-event count.
        int Send(NativeKeyEvent[] events);
    }

    internal interface INativeKeyboardEditorPreparation
    {
        void PrepareEditor(NativeKeyboardState state, NativeWriteOperation operation, Request request,
                           List<string> preparation);
    }
    // One fresh sample for one typed scalar/selection guard, inside the same
    // native execute call. No result grants authority to a later scalar.
    // Composite discovery and clipboard sequences retain their original path.
    internal interface INativeTypedTextDispatchSequence : INativeKeyboardSequence
    {
        bool SupportsDispatchSampling { get; }
        void CheckNativeRecipient(Request request);
        NativeKeyboardState ReadDispatchState(NativeInputContract contract, Request request);
        void ValidateDispatchScope(Request request);
        void ValidateDispatchReceiver(Request request);
        void ValidateDispatchExplicitScope(Request request);
        void ValidateDispatchFocus(Request request);
        void ValidateDispatchQueue(NativeKeyboardState state, Request request);
    }
}
