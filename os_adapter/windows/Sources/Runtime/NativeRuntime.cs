// Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

using System;
using System.Collections.Generic;
using System.Linq;

namespace ComputerUse.Windows
{
    // NativeResourceScope owner across begin/inspect/execute/finish frames.
    // Query/prepare/dispatch subpipelines below borrow this state on the existing
    // single native executor; none independently owns leases or replay history.
    internal sealed class NativeRuntime : INativeRuntime, INativeWorkerHealthStatus
    {
        static readonly string[] ReadCapabilities = { "cancel",
                                                      "requestDedup",
                                                      "nativeOperations",
                                                      "nativeDirectory",
                                                      "nativeRelations",
                                                      "nativeNodePages",
                                                      "nativeNodeDepthBudget",
                                                      "nativeRelationPages",
                                                      "nativeCapturedText",
                                                      "nativeTableFacts",
                                                      "nativeAdvertisedFields",
                                                      "nativeFollowRelations",
                                                      "nativeUiaReadV2",
                                                      "nativeUiaTextContext",
                                                      "nativeUiaTextLines",
                                                      "nativeWindowContent",
                                                      "nativeRawNodeMetadata",
                                                      "nativeObservationScopes",
                                                      "nativeReadDiscovery" };

        public string[] Capabilities
        {
            get {
                return ReadCapabilities.Concat(writer == null ? new string[0] : writer.Capabilities)
                    .Concat(reader is INativeReadEventBackend ? new[] { "nativeReadEvents" } : new string[0])
                    .Concat(capture == null ? new string[0] : new[] { "nativeCapture", "nativeCaptureKinds" })
                    .ToArray();
            }
        }

        readonly Func<object, Dictionary<string, object>> binding;
        readonly INativeReadBackend reader;
        readonly NativeQueryPipeline queries;
        readonly NativeCapturePipeline captures;
        readonly NativeExecution writer;
        readonly INativeCaptureBackend capture;
        readonly Action<string, Dictionary<string, object>, NativePointerPoint, bool, int, bool> displayPointer;
        readonly Dictionary<string, string> finished = new Dictionary<string, string>();
        readonly Queue<string> finishedOrder = new Queue<string>();
        string operation;
        double deadline;
        Dictionary<string, object> original;
        NativeWriteOperation write;
        IDisposable lease;
        bool retired;
        public string WorkerRetirementReason { get; private set; }
        void PromoteWorkerRetirement()
        {
            if (write == null || write.WorkerRetirementReason == null)
                return;
            if (WorkerRetirementReason == null)
                WorkerRetirementReason = write.WorkerRetirementReason;
            retired = true;
        }
        void PreserveRetiredReceipt(Dictionary<string, object> result)
        {
            if (WorkerRetirementReason == null)
                return;
            result["cleanup"] = "failed";
            result["retryReason"] = null;
            if (result["errorCode"] == null)
            {
                result["errorCode"] = "execution_unknown";
                result["detail"] = (string)result["detail"] +
                                   " Native worker preparation cleanup is unconfirmed; reconnect before further input.";
            }
        }
        sealed class Step
        {
            internal Dictionary<string, object> Command;
            internal object Result;
        }

        readonly Dictionary<long, Step> steps = new Dictionary<long, Step>();
        readonly Queue<long> stepOrder = new Queue<long>();
        long highStep;
        internal NativeRuntime(
            string session, INativeReadBackend reader, NativeExecution writer = null,
            INativeCaptureBackend capture = null,
            Action<string, Dictionary<string, object>, NativePointerPoint, bool, int, bool> displayPointer = null)
        {
            binding = raw => NativeBinding.Resolve(raw, session);
            this.reader = reader;
            this.writer = writer;
            this.capture = capture;
            this.displayPointer = displayPointer;
            captures = new NativeCapturePipeline(reader, capture, binding, FinishCurrent, Expire);
            queries = new NativeQueryPipeline(
                reader, binding,
                writer == null ? null
                               : new Func<NativeWriteOperation, Dictionary<string, object>, string[], Request, object>(
                                     writer.Inspect),
                FinishCurrent, Expire,
                writer == null ? null
                               : new Func<NativeWriteOperation, Dictionary<string, object>, Request, object>(
                                     writer.InspectCurrentReceiver));
        }

        void FinishCurrent()
        {
            if (operation == null)
                return;
            PromoteWorkerRetirement();
            string closed = operation, cleanup = write == null ? "complete" : write.Cleanup;
            if (cleanup != "complete")
                retired = true;
            operation = null;
            original = null;
            write = null;
            steps.Clear();
            stepOrder.Clear();
            // Do not enter COM/provider/lease cleanup after unsafe thread state.
            // The outer process owner must confirm exit before accepting more work.
            if (WorkerRetirementReason == null)
                cleanup = NativeResourcePipeline.Release(cleanup, reader, ref lease, ref retired);
            else
            {
                cleanup = "failed";
                lease = null;
            }
            finished[closed] = cleanup;
            finishedOrder.Enqueue(closed);
            while (finishedOrder.Count > 8)
                finished.Remove(finishedOrder.Dequeue());
        }

        public void Close()
        {
            try
            {
                FinishCurrent();
            }
            finally
            {
                if (WorkerRetirementReason == null)
                {
                    var events = reader as INativeReadEventBackend;
                    if (events != null)
                        events.Dispose();
                }
            }
        }

        public void Expire()
        {
            PromoteWorkerRetirement();
            if (operation != null && (WorkerRetirementReason != null || Wire.Now >= deadline))
                FinishCurrent();
        }

        public object Perform(string method, Dictionary<string, object> p, Request r)
        {
            PromoteWorkerRetirement();
            if (WorkerRetirementReason != null && method != "finish")
                throw new Failure("execution_unknown",
                                  "native worker preparation cleanup is unconfirmed; this runtime is retired");
            try
            {
                return PerformCore(method, p, r);
            }
            catch (NativeDesktopCleanupFailure error)
            {
                // Read/begin failures can precede a write owner. Retain health
                // in this existing runtime, never in a static desktop helper.
                if (write != null)
                    write.RetireWorker(error.Reason);
                if (WorkerRetirementReason == null)
                    WorkerRetirementReason = error.Reason;
                retired = true;
                var primary = error.OriginalFailure;
                throw new Failure(primary == null ? "execution_unknown" : primary.Code, error.Message,
                                  primary == null ? "reconnect and observe; do not replay the original input"
                                                  : primary.Next);
            }
            finally
            {
                PromoteWorkerRetirement();
            }
        }
        object PerformCore(string method, Dictionary<string, object> p, Request r)
        {
            if (method == "finish")
            {
                Wire.Keys(p, "operation");
                string id = Wire.Str(p, "operation");
                if (id == operation)
                    FinishCurrent();
                if (!finished.ContainsKey(id))
                    throw new Failure("stale_target", "native operation is unknown or expired");
                return Wire.Obj("cleanup", finished[id]);
            }

            if (method == "begin")
            {
                Wire.Keys(p, "mode,target,intent", "clickContract");
                string mode = Wire.Str(p, "mode");
                var intent = Wire.Object(p["intent"]);
                Wire.Keys(intent, "kind,parameters");
                string kind = Wire.Str(intent, "kind");
                var parameters = Wire.Object(intent["parameters"]);
                var target = binding(p["target"]);
                bool writable = mode == "write";
                bool clickV1 = p.ContainsKey("clickContract");
                if (clickV1 && (Wire.Str(p, "clickContract") != "v1" || !writable || kind != "click"))
                    throw new Failure("invalid_request", "clickContract v1 requires a write click operation");
                if (writable)
                {
                    if (writer == null)
                        throw new Failure("mode_unsupported", "this native driver has no input execution provider");
                    writer.Validate(intent, target);
                }
                else if (mode != "read" || kind != "read" || parameters.Count != 0)
                    throw new Failure("invalid_request", "read operation requires an empty read intent");
                if (!writable && target["capture"] != null)
                    throw new Failure("invalid_request", "a capture binding is an input scope, not a new read scope");
                Expire();
                if (retired)
                    throw new Failure("execution_unknown", "native cleanup failed; this runtime is retired");
                if (operation != null)
                    throw new Failure("busy", "another native operation is active");
                r.Check();
                NativeResourcePipeline.Acquire(writable, target, r, reader,
                                               writer == null ? null : new Func<Request, IDisposable>(writer.Acquire),
                                               ref lease, ref retired);
                operation = Guid.NewGuid().ToString("N");
                original = target;
                deadline = r.Deadline;
                highStep = 0;
                write = writable ? new NativeWriteOperation(target, intent, clickV1) : null;
                if (write != null && displayPointer != null)
                {
                    string displayOperation = operation;
                    var displayTarget = new Dictionary<string, object>(target);
                    write.DisplayPointer = (point, pulse, count, dragging) =>
                        displayPointer(displayOperation, displayTarget, point, pulse, count, dragging);
                }

                var begun = Wire.Obj("operation", operation, "target", new Dictionary<string, object>(original),
                                     "remainingMs", Math.Max(0, (int)Math.Floor(deadline - Wire.Now)));
                if (clickV1)
                    begun["clickContract"] = "v1";
                return begun;
            }

            if (method == "execute")
                return Execute(p, r);
            if (method == "capture")
                return captures.Run(p, r, new NativeOperationView(operation, original, write, deadline));
            if (method != "inspect")
                throw new Failure("invalid_request", "v5 never dispatches legacy read or input commands");
            return queries.Run(p, r, new NativeOperationView(operation, original, write, deadline));
        }

        object Execute(Dictionary<string, object> p, Request r)
        {
            if (writer == null)
                throw new Failure("mode_unsupported", "this native driver has no input execution provider");
            Wire.Keys(p, "operation,step,command");
            string id = Wire.Str(p, "operation");
            long step = (long)Wire.Num(p, "step", 1, 9007199254740991, true);
            var command = Wire.Object(p["command"]);
            Wire.Keys(command, "kind,primitiveId,target,foreground");
            Wire.Str(command, "kind");
            Wire.Str(command, "primitiveId");
            Wire.Bool(command, "foreground");
            var target = binding(command["target"]);
            if (operation == null || id != operation || write == null)
                throw new Failure("stale_target", "native write operation is unknown or expired");
            Step prior;
            if (steps.TryGetValue(step, out prior))
            {
                bool same = command.Keys.All(k => k == "target"
                                                      ? NativeWriteOperation.Same(target, Wire.Object(prior.Command[k]))
                                                      : Equals(command[k], prior.Command[k]));
                if (!same)
                    throw new Failure("request_conflict", "step id reused with different input");
                return prior.Result;
            }

            if (step <= highStep)
                throw new Failure("request_conflict", "step result expired; input will not be repeated");
            if (write.InputTerminated)
            {
                r.Effect = true;
                throw new Failure(
                    "execution_unknown",
                    "this operation has unresolved input or incompatible preparation; it cannot send again");
            }

            r.Deadline = Math.Min(r.Deadline, deadline);
            highStep = step;
            bool cleanupFailure = false;
            try
            {
                r.Check();
                object result = writer.Execute(write, command, r, operation, step);
                PromoteWorkerRetirement();
                PreserveRetiredReceipt(Wire.Object(result));
                var copy = new Dictionary<string, object>(command);
                copy["target"] = new Dictionary<string, object>(target);
                steps[step] = new Step { Command = copy, Result = result };
                stepOrder.Enqueue(step);
                while (stepOrder.Count > 32)
                    steps.Remove(stepOrder.Dequeue());
                var code = Wire.Object(result)["errorCode"] as string;
                if (code == "cancelled" || code == "deadline_exceeded" || write.Cleanup != "complete")
                    FinishCurrent();
                return result;
            }
            catch (NativeDesktopCleanupFailure)
            {
                cleanupFailure = true;
                throw;
            }
            catch (Failure error)
            {
                if (error.Code == "cancelled" || error.Code == "deadline_exceeded")
                    FinishCurrent();
                throw;
            }
            // Carry only this pending exception to Perform's existing owner.
            finally
            {
                if (!cleanupFailure)
                    Expire();
            }
        }
    }
}
