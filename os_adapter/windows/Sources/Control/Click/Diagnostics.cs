// Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

using System;
using System.Collections.Generic;

namespace ComputerUse.Windows
{
    // Per-attempt diagnostic projection only. It owns no input/attempt state;
    // every value is copied from the selected contract or final receipt.
    // The operation/step pair also correlates across the isolated worker's
    // private request IDs, which must not be reported as public request IDs.
    internal sealed class NativeClickDiagnostics
    {
        readonly string operation;
        readonly long? step;
        readonly Dictionary<string, object> command;
        readonly List<object> phases = new List<object>();
        readonly double started;
        string family = "windows.unresolved", delivery = "unknown";
        NativeClickDiagnostics(string operation, long? step, Dictionary<string, object> command)
        {
            this.operation = operation;
            this.step = step;
            this.command = command;
            started = Wire.Now;
        }

        internal static NativeClickDiagnostics Start(string operation, long? step, Dictionary<string, object> command)
        {
            try
            {
                return Environment.GetEnvironmentVariable("COMPUTER_USE_TRACE_NATIVE_INPUT") == "1" ? new NativeClickDiagnostics(operation, step, command) : null;
            }
            catch
            {
                return null;
            }
        }

        sealed class Interval : IDisposable
        {
            readonly NativeClickDiagnostics owner;
            readonly string name;
            readonly double started;
            internal Interval(NativeClickDiagnostics owner, string name)
            {
                this.owner = owner;
                this.name = name;
                started = Wire.Now;
            }

            public void Dispose()
            {
                try
                {
                    owner.phases.Add(Wire.Obj("name", name, "durationMs", Math.Max(0, Wire.Now - started)));
                }
                catch
                {
                } // Clock/diagnostic bookkeeping must never mask input failures.
            }
        }

        internal static IDisposable Measure(NativeClickDiagnostics owner, string name)
        {
            try
            {
                return owner == null ? null : new Interval(owner, name);
            }
            catch
            {
                return null;
            }
        }

        internal static void Select(NativeClickDiagnostics owner, NativeClickOffer offer)
        {
            if (owner == null)
                return;
            try
            {
                owner.family = offer.RouteFamily;
                owner.delivery = offer.Delivery;
            }
            catch
            {
            }
        }

        internal static void Finish(NativeClickDiagnostics owner, Dictionary<string, object> result, bool completed)
        {
            if (owner == null)
                return;
            try
            {
                var target = Wire.Object(owner.command["target"]);
                object evidence;
                result.TryGetValue("click", out evidence);
                // Existing stderr diagnostics are forwarded by the worker
                // supervisor. No protocol field or UI observation is added.
                Console.Error.WriteLine(Wire.Json(Wire.Obj("kind", "native_click_metrics", "version", 1, "operation", owner.operation, "step", owner.step, "primitiveId", owner.command["primitiveId"], "routeFamily", owner.family, "delivery", owner.delivery, "target", new Dictionary<string, object>(target), "coordinateSource", owner.delivery == "object_action" ? "none" : target["capture"] != null ? "capture" : target["node"] != null ? "element" : "none", "foregroundPreparationAllowed", owner.command["foreground"], "status", completed ? "returned" : "interrupted", "durationMs", Math.Max(0, Wire.Now - owner.started), "phases", owner.phases, "dispatch", result["dispatch"], "cleanup", result["cleanup"], "errorCode", result["errorCode"], "retryReason", result["retryReason"], "click", evidence)));
            }
            catch
            {
            } // Includes serialization and a closed/failing stderr sink.
        }
    }
}
