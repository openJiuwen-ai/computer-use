// Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

using System;
using System.Collections.Generic;
using System.Linq;

namespace ComputerUse.Windows
{
    // Input/output: ObjectDispatchPlan Run(NativeWriteOperation operation, Dictionary<string, object> command, Dictionary<string, object> target, string unit, string id). Borrows only the named native capability.
    // Failures propagate unchanged; this stage never retries or selects a route.
    // State writes: only returned/local facts or the passed receipt; no ownership transfer.
    // Failure boundary: original native errors propagate to the owning scope/receipt.
    internal static class ResolveNativeTargetPass
    {
        internal const string Effects = "PURE";
        internal static ObjectDispatchPlan Run(NativeWriteOperation operation, Dictionary<string, object> command, Dictionary<string, object> target, string unit, string id)
        {
            NativeInputContract contract;
            if (!operation.Contracts.TryGetValue(id, out contract) || contract.Unit != unit || !NativeWriteOperation.Same(contract.Target, target))
                throw new Failure("stale_target", "the native contract expired or belongs to another target");
            if ((bool)command["foreground"])
                throw new Failure("invalid_request", "an addressed native object action does not request foreground preparation");
            string kind = (string)operation.Intent["kind"], text = null;
            if (unit == "native_action")
            {
                if (!new[]
                {
                    "click",
                    "openMenu",
                    "action"
                }.Contains(kind) || kind == "action" && (string)Wire.Object(operation.Intent["parameters"])["action"] != contract.Action)
                    throw new Failure("invalid_request", "the native action does not match the original intent");
            }
            else if (unit == "native_text_edit" && kind == "replace")
                text = (string)Wire.Object(operation.Intent["parameters"])["text"];
            else
                throw new Failure("invalid_request", "the native unit cannot implement this intent");
            return new ObjectDispatchPlan(contract, target, unit, text);
        }
    }
}
