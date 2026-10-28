// Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

using System;
using System.Collections.Generic;
using System.Linq;
using System.Threading;

namespace ComputerUse.Windows
{
    // Public request journaling lives in Driver. A replaceable worker owns all
    // UIA objects. Only verified Win32 window identities survive a read timeout;
    // element, text, image and operation tokens are never silently reconstructed.
    internal sealed class NativeIsolatedRuntime : INativeRuntime, INativeWorkerHealthStatus
    {
        readonly string session, mode;
        readonly AgentPointer pointer = new AgentPointer();
        INativeWorkerProcess child;
        readonly Func<INativeWorkerProcess> createChild;
        public string WorkerRetirementReason { get; private set; }
        internal bool WorkerExitConfirmed { get; private set; }
        internal string WorkerDisposalFailure { get; private set; }
        string retiredOperation;
        string retiredCleanup;
        readonly Dictionary<string, Dictionary<string, object>> windows =
            new Dictionary<string, Dictionary<string, object>>();
        readonly Dictionary<string, long> generations = new Dictionary<string, long>();
        readonly HashSet<string> completedReads = new HashSet<string>();
        readonly Queue<string> completedOrder = new Queue<string>();
        string operation;
        bool writing, retired, recycleRequested;
        double readDeadline;
        long sequence;
        string[] capabilities;
        public string[] Capabilities
        {
            get {
                return capabilities.Concat(new[] { "nativeReadIsolation", "nativeWindowReadiness" }).ToArray();
            }
        }
        internal NativeIsolatedRuntime(string session, string mode, Func<INativeWorkerProcess> createChild = null)
        {
            this.session = session;
            this.mode = mode;
            this.createChild = createChild ?? (() => new NativeChildProcess("--native-worker"));
            try
            {
                Start(new Request(CancellationToken.None, 8000));
            }
            catch
            {
                pointer.Dispose();
                throw;
            }
        }

        bool Valid(Dictionary<string, object> item, long generation)
        {
            try
            {
                new Window { Handle = new IntPtr(Convert.ToInt64(item["handle"])), Pid = Convert.ToUInt32(item["pid"]),
                             Started = Int64.Parse((string)item["started"]), Generation = generation }
                    .Validate();
                return true;
            }
            catch
            {
                return false;
            }
        }

        void Start(Request request)
        {
            if (retired)
                throw new Failure("execution_unknown", "native worker is retired; reconnect before further operations");
            if (child != null)
                return;
            foreach (string id in windows.Keys.ToArray())
                if (!Valid(windows[id], generations[id]))
                {
                    windows.Remove(id);
                    generations.Remove(id);
                }
            child = createChild();
            sequence = 0;
            try
            {
                child.Send(Wire.Obj("session", session, "windows", windows.Values.ToArray()));
                var ready = Receive(request, Wire.Now + 5000);
                if (!ready.ContainsKey("ready") || !Equals(ready["ready"], true))
                    throw new InvalidOperationException("native worker bootstrap failed");
                child.Send(Wire.Obj("version", 6, "id", 0, "method", "hello", "params", Wire.Obj("desktopMode", mode)));
                var hello = Receive(request, Wire.Now + 5000);
                capabilities = Wire.Array(Wire.Object(hello["result"])["capabilities"]).Cast<string>().ToArray();
            }
            catch
            {
                RetireWorker();
                throw;
            }
        }

        Dictionary<string, object> Receive(Request request, double limit)
        {
            while (true)
            {
                request.Check();
                if (Wire.Now >= limit)
                    throw new Failure("observation_unavailable",
                                      "native provider did not return within the isolated read budget");
                string line = child.Poll(20);
                if (line != null)
                {
                    var value = Wire.Object(Wire.Serializer().DeserializeObject(line));
                    if (value.ContainsKey("visualPointer"))
                    {
                        try
                        {
                            pointer.Notice(value["visualPointer"]);
                        }
                        catch
                        {
                        }

                        continue;
                    }

                    return value;
                }
            }
        }

        void Checkpoint(object raw, Dictionary<IntPtr, long> before)
        {
            foreach (object value in Wire.Array(raw))
            {
                var item = Wire.Object(value);
                string id = (string)item["surface"];
                var handle = new IntPtr(Convert.ToInt64(item["handle"]));
                long generation;
                if (!generations.TryGetValue(id, out generation) && !before.TryGetValue(handle, out generation))
                    generation = 0;
                // A destroy during this request is ambiguous even if the HWND
                // has since been reused. Never import it into a fresh worker.
                if (!Valid(item, generation))
                {
                    windows.Remove(id);
                    generations.Remove(id);
                    continue;
                }

                windows[id] = item;
                generations[id] = generation;
            }
        }
        bool StopOwnedWorker(int grace = 0)
        {
            if (child == null)
                return WorkerExitConfirmed;
            bool confirmed = false;
            try
            {
                confirmed = child.Stop(grace) && child.ExitConfirmed;
            }
            catch
            {
            }
            if (!confirmed)
                return false;
            WorkerExitConfirmed = true;
            var stopped = child;
            child = null;
            // Process exit is proven; disposal cannot erase the original receipt
            // or upgrade the operation's failed preparation-cleanup evidence.
            try
            {
                stopped.Dispose();
            }
            catch (Exception error)
            {
                WorkerDisposalFailure = error.GetType().Name + ": " + error.Message;
                retired = true;
            }
            return true;
        }
        void RetireWorker()
        {
            if (child == null)
                return;
            if (!StopOwnedWorker())
            {
                retired = true;
                throw new Failure("execution_unknown", "isolated native worker termination was not confirmed");
            }
            if (WorkerDisposalFailure != null)
                throw new Failure("execution_unknown",
                                  "isolated native worker exited, but process-resource cleanup failed");
        }
        void RetireUnsafeWorker(string reason, Dictionary<string, object> response)
        {
            // Sticky before Stop: exceptions/false results never reopen this session.
            retired = true;
            if (WorkerRetirementReason == null)
                WorkerRetirementReason = reason;
            retiredOperation = operation;
            retiredCleanup = "failed";
            bool stopped = StopOwnedWorker();
            try
            {
                pointer.End(false);
            }
            catch
            {
            }
            object raw;
            if (response.TryGetValue("result", out raw))
            {
                var receipt = Wire.Object(raw);
                // Retain original dispatch, preparation and error evidence. A
                // poisoned success must still fail closed through existing fields.
                if (receipt.ContainsKey("cleanup"))
                    receipt["cleanup"] = "failed";
                if (receipt.ContainsKey("retryReason"))
                    receipt["retryReason"] = null;
                if (receipt.ContainsKey("errorCode") && receipt["errorCode"] == null)
                    receipt["errorCode"] = "execution_unknown";
                if (!stopped && receipt.ContainsKey("detail"))
                    receipt["detail"] = (string)receipt["detail"] +
                                        " Native worker termination was not confirmed; reconnect before further input.";
            }
            else if (!stopped && response.TryGetValue("error", out raw))
            {
                var error = Wire.Object(raw);
                error["detail"] = (string)error["detail"] + " Native worker termination was not confirmed.";
                error["next"] = "reconnect and observe; do not replay the original input";
            }
        }

        void EndInterruptedRead()
        {
            // Never acknowledge cleanup before the old process is confirmed
            // gone. Failure leaves this session retired, not safely resumable.
            RetireWorker();
            if (operation != null)
            {
                completedReads.Add(operation);
                completedOrder.Enqueue(operation);
                operation = null;
                while (completedOrder.Count > 64)
                    completedReads.Remove(completedOrder.Dequeue());
            }

            recycleRequested = false;
        }

        public object Perform(string method, Dictionary<string, object> parameters, Request request)
        {
            using (var isolation = method == "capture" ? pointer.IsolateCapture() : null)
            {
                try
                {
                    return PerformCore(method, parameters, request);
                }
                catch
                {
                    try
                    {
                        if (writing || method == "begin")
                            pointer.Outcome(false);
                        if (retired || child == null)
                            pointer.End(false);
                    }
                    catch
                    {
                    }

                    throw;
                }
            }
        }

        object PerformCore(string method, Dictionary<string, object> parameters, Request request)
        {
            if (method == "finish" && retiredOperation != null && Wire.Str(parameters, "operation") == retiredOperation)
                return Wire.Obj("cleanup", retiredCleanup);
            if (method == "finish" && completedReads.Contains(Wire.Str(parameters, "operation")))
                return Wire.Obj("cleanup", "complete");
            if (recycleRequested)
            {
                if (method == "finish" && Wire.Str(parameters, "operation") == operation)
                {
                    EndInterruptedRead();
                    return Wire.Obj("cleanup", "complete");
                }

                // A directory stage may request applications then surfaces in
                // one read lease. Drain only their Win32 metadata; the Python
                // caller finishes this lease before reading trees or pixels.
                var query = method == "inspect" ? Wire.Object(parameters["query"]) : null;
                if (query == null || !new[] { "applications", "surfaces" }.Contains(Wire.Str(query, "kind")))
                    throw new Failure(
                        "stale_target",
                        "UIA enrichment failed; finish this read lease before collecting more observations");
            }

            Start(request);
            bool writeRequest = writing || method == "begin" && Equals(parameters["mode"], "write");
            long id = ++sequence;
            var before = Native.GenerationSnapshot();
            child.Send(Wire.Obj("version", 6, "session", session, "id", id, "method", method, "params", parameters,
                                "timeoutMs", Math.Max(1, Math.Min(120000, (int)(request.Deadline - Wire.Now)))));
            Dictionary<string, object> response;
            try
            {
                // Input retains its own deadline/cleanup contract. This limit
                // applies only to observations and never replays a command.
                response =
                    Receive(request, writeRequest ? request.Deadline
                                                  : Math.Min(request.Deadline, Wire.Now + Request.IsolatedReadMs));
            }
            catch (Exception error)
            {
                if (writeRequest)
                {
                    request.Effect = method == "execute";
                    try
                    {
                        child.Send(Wire.Obj("version", 6, "session", session, "cancel", id));
                    }
                    catch
                    {
                    }

                    // Let native units release paired keys before terminating.
                    retired = true;
                    if (WorkerRetirementReason == null)
                        WorkerRetirementReason = "native write worker did not return";
                    retiredOperation = operation;
                    retiredCleanup = "unknown";
                    bool stopped = StopOwnedWorker(750);
                    throw new Failure(
                        "execution_unknown",
                        "native write worker stopped responding; no command was replayed; reconnect and observe" +
                            (stopped ? "" : "; worker termination was not confirmed"));
                }

                EndInterruptedRead();
                var failure = error as Failure;
                if (failure != null && (failure.Code == "cancelled" || failure.Code == "deadline_exceeded"))
                    throw;
                throw new Failure(
                    "observation_unavailable",
                    "read worker was stopped after a blocked or failed provider call; the selected window is retained, old element/text/image references require a fresh read. " +
                        error.Message);
            }

            if (Convert.ToInt64(response["id"]) != id || !Equals(response["session"], session))
            {
                RetireWorker();
                throw new Failure("observation_unavailable", "native worker response identity mismatch");
            }
            object retirement;
            if (response.TryGetValue("workerRetirement", out retirement))
            {
                string reason = "invalid native worker retirement evidence";
                try
                {
                    var health = Wire.Object(retirement);
                    Wire.Keys(health, "reason,cleanup");
                    if (Wire.Str(health, "cleanup") == "failed")
                        reason = Wire.Str(health, "reason");
                }
                catch
                {
                }
                // Even malformed health evidence invalidates the worker; do not
                // parse a checkpoint or run another request to investigate it.
                RetireUnsafeWorker(reason, response);
            }
            else
            {
                Checkpoint(response["windowCheckpoint"], before);
                try
                {
                    pointer.Checkpoint(windows, generations);
                    if (response.ContainsKey("pointerCheckpoint"))
                        pointer.Notice(response["pointerCheckpoint"]);
                }
                catch
                {
                }
                if (response.ContainsKey("recycleReadWorker") && Equals(response["recycleReadWorker"], true))
                {
                    if (writeRequest)
                    {
                        retired = true;
                        RetireWorker();
                        throw new Failure("execution_unknown",
                                          "native write worker unexpectedly requested read recovery");
                    }
                    recycleRequested = true;
                }
            }

            object raw;
            if (response.TryGetValue("error", out raw))
            {
                var error = Wire.Object(raw);
                request.Effect = !Equals(error["effect"], "none");
                throw new Failure((string)error["code"], (string)error["detail"], (string)error["next"]);
            }

            var result = response["result"];
            if (method == "begin")
            {
                operation = (string)Wire.Object(result)["operation"];
                writing = Equals(parameters["mode"], "write");
                readDeadline = request.Deadline;
                if (writing)
                    try
                    {
                        pointer.Begin(operation, Wire.Object(parameters["target"]), Wire.Object(parameters["intent"]));
                    }
                    catch
                    {
                    }
            }
            if (!retired && (method == "inspect" || method == "capture"))
                try
                {
                    pointer.Observe(method, parameters, result);
                }
                catch
                {
                }

            if (method == "finish")
            {
                pointer.End();
                operation = null;
                writing = false;
            }

            if (method == "execute" && Wire.Object(result)["errorCode"] != null)
                pointer.Outcome(false);
            return result;
        }

        public void Expire()
        {
            if (retired)
                return;
            if (writing && Wire.Now >= readDeadline)
                pointer.End(false);
            if (recycleRequested && Wire.Now >= readDeadline)
                EndInterruptedRead();
        }

        internal void StopDisplay()
        {
            pointer.Dispose();
        }

        public void Close()
        {
            pointer.Dispose();
            if (child != null)
            {
                bool stopped = false;
                try
                {
                    stopped = child.Stop(750) && child.ExitConfirmed;
                }
                catch
                {
                }
                WorkerExitConfirmed = stopped;
                if (!stopped)
                    retired = true;
                // Disposing the owned job is final containment; do not turn it
                // into a claim that the earlier stop or detach was confirmed.
                try
                {
                    child.Dispose();
                }
                catch (Exception error)
                {
                    WorkerDisposalFailure = error.GetType().Name + ": " + error.Message;
                    retired = true;
                }
                finally
                {
                    child = null;
                }
            }
        }
    }
}
