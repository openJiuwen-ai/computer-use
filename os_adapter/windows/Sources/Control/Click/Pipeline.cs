// Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

using System;
using System.Collections.Generic;
using System.Linq;

namespace ComputerUse.Windows
{
    internal sealed class NativeClickPipeline
    {
        readonly NativeObjectPipeline objects;
        readonly NativePointer pointer;
        internal NativeClickPipeline(NativeObjectPipeline objects, NativePointer pointer)
        {
            this.objects = objects;
            this.pointer = pointer;
        }

        internal object Execute(NativeWriteOperation operation, Dictionary<string, object> command, Request r, string operationId = null, long? step = null)
        {
            var result = Wire.Obj("target", new Dictionary<string, object>(Wire.Object(command["target"])), "dispatch", "notDispatched", "detail", "No requested click dispatched.", "preparation", new string[0], "confirmation", "unavailable", "cleanup", "complete", "errorCode", null, "retryReason", null, "context", null);
            var diagnostics = NativeClickDiagnostics.Start(operationId, step, command);
            bool completed = false;
            try
            {
                string phase = "resolve";
                string family = "windows.unresolved";
                var previous = r.ClickPreparation;
                r.ClickPreparation = operation.Click;
                try
                {
                    NativeInputContract contract;
                    using (NativeClickDiagnostics.Measure(diagnostics, "resolve"))
                    {
                        contract = ResolveClickContractPass.Run(operation, command);
                    }

                    family = contract.Click.RouteFamily;
                    NativeClickDiagnostics.Select(diagnostics, contract.Click);
                    operation.Click.StartAttempt();
                    if (contract.Unit == "native_action")
                    {
                        // This span includes the existing UIA query/prepare/final
                        // guard/send boundary. Keep those calls adjacent.
                        using (NativeClickDiagnostics.Measure(diagnostics, "uiaObject"))
                            result = Wire.Object(objects.Execute(operation, command, r));
                    }
                    else
                    {
                        if (pointer == null || contract.Pointer == null || !contract.Supported)
                            throw new Failure("input_unavailable", "the selected pointer mechanism is unavailable");
                        phase = "sample";
                        using (NativeClickDiagnostics.Measure(diagnostics, "sampleBeforePreparation"))
                            RefreshClickPointPass.Run(contract, pointer, r);
                        phase = "prepare";
                        using (NativeClickDiagnostics.Measure(diagnostics, "prepare"))
                            PrepareClickReceiverPass.Run(operation, contract, (bool)command["foreground"], pointer, r);
                        phase = "sample";
                        using (NativeClickDiagnostics.Measure(diagnostics, "sampleAfterPreparation"))
                            RefreshClickPointPass.Run(contract, pointer, r);
                        phase = "check";
                        using (NativeClickDiagnostics.Measure(diagnostics, "finalCheck"))
                            CheckClickDispatchPass.Run(contract, pointer, r);
                        phase = "dispatch";
                        using (NativeClickDiagnostics.Measure(diagnostics, "dispatchAndRelease"))
                            SendClickPointerPass.Run(operation, contract, pointer, r);
                        result["detail"] = NativePointer.ClickDetail(contract);
                    }
                }
                catch (Exception error)
                {
                    if (operation.Click.CallInFlight)
                        operation.Click.Unknown();
                    var failure = error as Failure;
                    result["errorCode"] = failure == null ? operation.Click.HasRequestedInput ? "execution_unknown" : "input_unavailable" : failure.Code;
                    result["detail"] = String.IsNullOrWhiteSpace(error.Message) ? "Native click failed." : error.Message;
                    if (operation.Click.Preparation.Any(p => p.Observation == "unknown" || p.Cleanup != "complete"))
                        operation.Click.RequireObservation();
                    if (phase != "dispatch" && !operation.Click.HasRequestedInput && operation.Click.ContinuationCompatible && (string)result["errorCode"] == "input_unavailable")
                        result["retryReason"] = phase == "prepare" ? "preparationUnavailable" : "routeUnavailable";
                }
                finally
                {
                    r.ClickPreparation = previous;
                }

                using (NativeClickDiagnostics.Measure(diagnostics, "buildReceipt"))
                {
                    result["dispatch"] = operation.Click.Dispatch;
                    result["preparation"] = operation.Click.AttemptPreparation.Select(p => p.Kind).Distinct().ToArray();
                    if (operation.Click.Preparation.Any(p => p.Cleanup != "complete"))
                        operation.Cleanup = "failed";
                    result["cleanup"] = operation.Cleanup;
                    if (operation.Cleanup != "complete" || !operation.Click.ContinuationCompatible)
                        result["retryReason"] = null;
                    result["click"] = operation.Click.Evidence(Wire.Object(result["target"]), family, result["retryReason"]);
                }

                completed = true;
                return result;
            }
            finally
            {
                NativeClickDiagnostics.Finish(diagnostics, result, completed);
            }
        }
    }
}
