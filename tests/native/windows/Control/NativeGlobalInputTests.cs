// Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

using System;
using System.Threading;
using System.Collections.Generic;

namespace ComputerUse.Windows
{
    internal static class NativeGlobalInputTests
    {
        static void Denied(Action call, string boundary)
        {
            try
            {
                call();
                throw new Exception(boundary + " admitted unauthorized global input");
            }
            catch (Failure error)
            {
                if (error.Code != "permission_required")
                    throw new Exception(boundary + " failed for the wrong reason: " + error.Code);
            }
        }

        sealed class Keyboard : INativeKeyboardProvider, INativeGlobalKeyboardAuthority
        {
            internal bool Allowed;
            internal int Activations, FocusChanges, Keys;
            readonly NativeKeyboardState state = new NativeKeyboardState {
                Enabled = true, Focusable = true, WindowRoute = true
            };
            public NativeKeyboardState Read(Dictionary<string, object> target, string[] within, Request r, bool text = false)
            { r.Check(); return state; }
            public void RequireGlobalInput() { NativeGlobalInput.Require(Allowed); }
            public void RequireIdle(NativeKeyEvent[] events, Request r)
            { r.Check(); if (events.Length != 0) RequireGlobalInput(); }
            public void Activate(NativeKeyboardState current, Request r)
            { Activations++; state.Foreground = true; }
            public void Focus(NativeKeyboardState current, Request r)
            { FocusChanges++; state.Focused = true; }
            public int Send(NativeKeyEvent[] events)
            { RequireGlobalInput(); Keys += events.Length; return events.Length; }
        }

        static void CheckPreparation(bool grant, string kind)
        {
            var provider = new Keyboard { Allowed = grant };
            var keyboard = new NativeKeyboard(provider);
            var target = Wire.Obj("node", "original", "surface", "window");
            var intent = Wire.Obj("kind", kind, "parameters", kind == "key" ? Wire.Obj("key", "Return") : Wire.Obj());
            var operation = new NativeWriteOperation(target, intent);
            var request = new Request(CancellationToken.None, 2000, grant);
            var facts = Wire.Object(keyboard.Inspect(operation, target, new string[0], request));
            var primitive = Wire.Object(Wire.Array(facts["primitives"])[0]);
            var command = Wire.Obj("kind", "prepare_receiver", "primitiveId", primitive["id"], "target", target,
                                   "foreground", true);
            var receipt = Wire.Object(keyboard.Execute(operation, command, request));
            if (kind == "key" && !grant)
            {
                if (!Equals(receipt["errorCode"], "permission_required") || provider.Activations != 0 ||
                    provider.FocusChanges != 0 || provider.Keys != 0 || request.Dispatched || request.Effect)
                    throw new Exception("Known global key route must deny before preparation or effects");
                return;
            }
            if (receipt["errorCode"] != null || provider.Activations != 1 || provider.FocusChanges != 1)
                throw new Exception("Authorized key preparation and ordinary focus must remain usable");
            if (kind == "key")
            {
                command["kind"] = "key_sequence";
                command["foreground"] = false;
                receipt = Wire.Object(keyboard.Execute(operation, command, request));
                if (receipt["errorCode"] != null || provider.Keys != 2 || !operation.Dispatched)
                    throw new Exception("An explicitly granted chord must dispatch once through its existing route");
            }
        }

        static void Main()
        {
            var request = new Request(CancellationToken.None, 1000);
            if (request.GlobalInputAllowed)
                throw new Exception("Requests must not infer global input authorization");
            Denied(() => NativeGlobalInput.Require(request.GlobalInputAllowed), "session default");
            NativeGlobalInput.Require(new Request(CancellationToken.None, 1000, true).GlobalInputAllowed);
            // No Win32 calls may be reached by these unauthorized branches;
            // running on a non-Windows host is an additional independent oracle.
            Denied(() => Native.SendInput(0, new Native.INPUT[0], 0, false), "last injection boundary");
            Denied(() => Native.Send(new Native.INPUT[0], request), "request dispatch boundary");
            if (request.Dispatched || request.Effect)
                throw new Exception("Authorization denial must not claim an effect");
            Denied(() => new NativeWindowsPointer(null, null).RequireIdle(request), "pointer preparation");
            Denied(() => new NativeDesktopKeyboard(null).RequireIdle(new NativeKeyEvent[0], request),
                   "desktop keyboard preparation");
            Denied(() => new NativeUiaKeyboard(null).RequireIdle(NativeKeys.Chord("Return"), request),
                   "addressed global keyboard preparation");
            if (NativeForegroundRecovery.Try(request, null) || request.Effect || request.ForegroundRecoveryAttempted)
                throw new Exception("Without authorization, recovery must not open the shell menu before Escape");
            // Permission is a channel guard, not a requirement for pure chord
            // planning or ordinary focus/object/message execution.
            if (NativeKeys.Chord("Return").Length != 2)
                throw new Exception("Permission must not disable pure input planning");
            CheckPreparation(false, "focus");
            CheckPreparation(false, "key");
            CheckPreparation(true, "key");
            Console.WriteLine("NativeGlobalInputTests: passed (default denial, final injection, preparation, no false effects)");
        }
    }
}
