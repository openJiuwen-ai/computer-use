// Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

using System;

namespace ComputerUse.Windows
{
    internal enum NativeTextDelivery
    {
        KeyboardInput,
        DirectedCharacters
    }
    internal sealed class NativeTextScalar
    {
        internal readonly string Utf16;
        internal readonly NativeKeyEvent[] KeyboardEvents;
        internal NativeTextScalar(string utf16, NativeKeyEvent[] keyboardEvents)
        {
            Utf16 = utf16;
            KeyboardEvents = keyboardEvents;
        }
    }
    internal sealed class NativeCharacterMessageResult
    {
        // A completed synchronous window-message call is not a keyboard event
        // count, a value-change confirmation, or a promise of application acceptance.
        internal readonly bool Completed;
        internal readonly string Detail;
        internal NativeCharacterMessageResult(bool completed, string detail = null)
        {
            Completed = completed;
            Detail = detail;
        }
    }
    internal interface INativeCharacterTextSequence
    {
        NativeTextDelivery SelectTextDelivery(Request request);
        void RequireCharacterIdle(Request request);
        // Only between two UTF16 units of an already-started scalar. Original
        // object/window/scope/writable identity remains mandatory; focus may
        // have moved, but input never follows it to another HWND.
        void ValidateCharacterContinuation(Request request);
        NativeCharacterMessageResult SendCharacter(char utf16Unit, int timeoutMs);
    }
    internal interface INativeCharacterMessagePort
    {
        NativeCharacterMessageResult Send(IntPtr originalWindow, char utf16Unit, int timeoutMs);
    }
    // RESOURCE port for the one recipe's clock/yield, never an input permit.
    // The production adapter delegates to the original Request deadline/token.
    internal interface INativeTextScheduler
    {
        double Now { get; }
        void Wait(Request request, int milliseconds);
    }
}
