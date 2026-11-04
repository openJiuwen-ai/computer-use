// Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

using System;

namespace ComputerUse.Windows
{
    internal sealed class NativeCharacterEvidence
    {
        internal string Role, Runtime, HwndRuntime;
        internal IntPtr RawWindow, NativeFocus;
        internal bool Composite, SameElement, Leaf, Standalone, Unicode, KnownEditFamily;
        internal bool? Focused, Enabled, Writable;
    }
    internal interface INativeCharacterRecipientQuery
    {
        void ValidateClassFamily(Request request);
        IntPtr RawWindow(Request request);
        bool IsLeaf(Request request);
        bool? HasFocus(Request request);
    }
    internal static class NativeCharacterEligibility
    {
        internal static bool Eligible(NativeCharacterEvidence evidence)
        {
            return !evidence.Composite && evidence.Role == "ControlType.Edit" && evidence.RawWindow != IntPtr.Zero &&
                   evidence.RawWindow == evidence.NativeFocus && !String.IsNullOrEmpty(evidence.Runtime) &&
                   evidence.Runtime == evidence.HwndRuntime && evidence.SameElement && evidence.Leaf &&
                   evidence.Standalone && evidence.Unicode && evidence.KnownEditFamily && evidence.Focused == true &&
                   evidence.Enabled == true && evidence.Writable == true;
        }
        // This proof is used only together with directed synchronous WM_CHAR,
        // never to accelerate global SendInput. Native focus is checked last
        // by the outer guard; dispatch remains addressed to the original HWND.
        internal static void ValidateFocus(INativeCharacterRecipientQuery query, IntPtr expected, Request request)
        {
            request.Check();
            try
            {
                query.ValidateClassFamily(request);
                request.Check();
                IntPtr current = query.RawWindow(request);
                request.Check();
                if (current == IntPtr.Zero || current != expected)
                    throw new Failure("input_unavailable",
                                      "the character recipient no longer has its exact native Edit HWND");
                bool leaf = query.IsLeaf(request);
                request.Check();
                if (!leaf)
                    throw new Failure("input_unavailable",
                                      "the native Edit gained UIA children; no further character messages sent");
                bool? focused = query.HasFocus(request);
                request.Check();
                if (focused != true)
                    throw new Failure("input_unavailable", "the native Edit no longer confirms UIA keyboard focus");
            }
            catch
            {
                request.Check();
                throw;
            }
        }
    }
}
