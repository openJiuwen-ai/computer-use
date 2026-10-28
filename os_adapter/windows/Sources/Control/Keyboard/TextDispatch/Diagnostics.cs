// Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

using System;
using System.Collections.Generic;

namespace ComputerUse.Windows
{
    // Optional attribution only, separate from untraced performance runs.
    // No text payload, retries, provider queries or cross-scalar state.
    internal sealed class NativeTextDispatchTiming
    {
        readonly Dictionary<string, object> milliseconds = new Dictionary<string, object>();
        readonly double started = Wire.Now;
        internal static NativeTextDispatchTiming Start()
        {
            return Environment.GetEnvironmentVariable("COMPUTER_USE_TRACE_NATIVE_TEXT_GUARD") == "1"
                       ? new NativeTextDispatchTiming()
                       : null;
        }
        sealed class Measurement : IDisposable
        {
            readonly NativeTextDispatchTiming timing;
            readonly string phase;
            readonly double started;
            internal Measurement(NativeTextDispatchTiming timing, string phase)
            {
                this.timing = timing;
                this.phase = phase;
                started = Wire.Now;
            }
            public void Dispose()
            {
                timing.milliseconds[phase] = Wire.Now - started;
            }
        }
        internal static IDisposable Measure(NativeTextDispatchTiming timing, string phase)
        {
            return timing == null ? null : new Measurement(timing, phase);
        }
        internal static void CharacterStrategy(bool directed, string reason = null, string family = null)
        {
            if (Environment.GetEnvironmentVariable("COMPUTER_USE_TRACE_NATIVE_TEXT_GUARD") != "1")
                return;
            try
            {
                Console.Error.WriteLine(
                    "native_text_delivery " +
                    Wire.Json(Wire.Obj("strategy", directed ? "directedUnicodeEditCharacters" : "keyboardInput",
                                       "reason", reason, "family", family)));
            }
            catch
            {
            }
        }
        internal static void Finish(NativeTextDispatchTiming timing, bool completed)
        {
            if (timing == null)
                return;
            try
            {
                Console.Error.WriteLine("native_text_guard " +
                                        Wire.Json(Wire.Obj("phasesMs", timing.milliseconds, "totalMs",
                                                           Wire.Now - timing.started, "completed", completed)));
            }
            catch
            {
            } // Diagnostics cannot change input effects or dispatch decisions.
        }
    }
}
