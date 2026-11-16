// Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

using System;
using System.Collections.Generic;
using System.Linq;
using System.Runtime.InteropServices;
using System.Text;

namespace ComputerUse.Windows
{
    // The installation token names only a Shell entry or executable. Once a
    // window appears, the existing read store owns its running identity.
    internal sealed class NativeApplicationLaunch
    {
        const int ConfirmationMs = 7500, PublicationMs = 250, MaxEntries = 4096;
        readonly string session;
        readonly NativeReadStore store;
        readonly Dictionary<string, NativeApplicationEntry> entries = new Dictionary<string, NativeApplicationEntry>();
        readonly Queue<string> order = new Queue<string>();

        internal NativeApplicationLaunch(string session, NativeReadStore store)
        {
            this.session = session;
            this.store = store;
        }

        string Retain(NativeApplicationEntry entry)
        {
            foreach (var retained in entries)
                if (entry.Same(retained.Value))
                    return retained.Key;
            string token = NativeReadStore.Token("installation");
            entries.Add(token, entry);
            order.Enqueue(token);
            if (order.Count > MaxEntries)
                entries.Remove(order.Dequeue());
            return token;
        }

        internal NativeApplicationEntry Require(string installation)
        {
            NativeApplicationEntry entry;
            if (!entries.TryGetValue(installation, out entry))
                throw new Failure("stale_target", "the Windows launch entry is no longer retained");
            return entry;
        }

        internal object Lookup(Dictionary<string, object> query, Request request)
        {
            double start = Wire.Now;
            var notes = new List<string>();
            string key = query.ContainsKey("launchSelector") ? "launchSelector" : "installation";
            string selector = Wire.Str(query, key);
            var rows = key == "launchSelector"
                           ? NativeApplicationShell.Find(selector.Trim(), request).Select(entry =>
                                 Wire.Obj("id", Retain(entry), "displayName", NativeReadStore.Text(entry.Name),
                                          "isRunning", false, "path", entry.Executable, "identifier", entry.ShellId))
                                     .ToArray()
                           : Running(Require(selector), request, notes);
            return Wire.Obj("kind", "applications", "session", session, "platform", "windows", "observed",
                            NativeReadBackend.Stamp(start), "stopReasons", notes, key, selector, "applications", rows);
        }

        internal object Inspect(NativeWriteOperation write, Dictionary<string, object> target, Request request)
        {
            request.Check();
            var entry = Require(Wire.Str(target, "application"));
            if (write.InputTerminated)
                throw new Failure("execution_unknown", "the launch request was already sent");
            var contract = write.Contracts.Values.SingleOrDefault();
            if (contract == null)
            {
                contract = new NativeInputContract
                {
                    Id = NativeReadStore.Token("primitive"), Unit = "launch_application", Target = target,
                    Supported = true
                };
                write.Contracts.Add(contract.Id, contract);
            }
            return Wire.Obj("kind", "launch", "binding", target, "observed", NativeReadStore.Token("observation"),
                            "targetLabel", NativeReadStore.Text(entry.Name), "primitives", new[] {
                                Wire.Obj("id", contract.Id, "unit", contract.Unit, "channel", "object",
                                         "globalInput", "no", "supported", "yes", "readiness", "yes",
                                         "pending", new string[0], "backgroundPreparation", "no")
                            });
        }

        internal object Execute(NativeWriteOperation write, Dictionary<string, object> target,
                                Dictionary<string, object> command, Request request)
        {
            NativeInputContract contract;
            if (!write.Contracts.TryGetValue(Wire.Str(command, "primitiveId"), out contract) ||
                !NativeWriteOperation.Same(contract.Target, target) ||
                Wire.Str(command, "kind") != contract.Unit || Wire.Bool(command, "foreground"))
                throw new Failure("invalid_request", "application startup requires its selected launch contract");
            var entry = Require(Wire.Str(target, "application"));
            var receipt = Wire.Obj("target", target, "dispatch", "notDispatched", "detail", "Windows startup did not send a request.",
                                   "preparation", new string[0], "confirmation", "unavailable", "cleanup", "complete",
                                   "errorCode", null, "retryReason", null, "context", null);
            try
            {
                double until = Math.Min(request.Deadline - PublicationMs, Wire.Now + ConfirmationMs);
                request.ObservationDeadline = until;
                var running = FindWindow(entry, request, verifyAbsence: true);
                if (running != null)
                {
                    receipt["detail"] = "A matching Windows application window was already open.";
                }
                else
                {
                    NativeApplicationShell.Open(entry, () => {
                        if (Wire.Now >= until)
                            throw new Failure("deadline_exceeded", "no budget remains to start the Windows application");
                        string reason;
                        if (!Native.Interactive(out reason, requireForeground: false))
                            throw new Failure("input_unavailable", reason);
                        request.Sending();
                        write.Dispatched = true;
                        receipt["dispatch"] = "unknown";
                    });
                    receipt["dispatch"] = "dispatched";
                    receipt["detail"] = "The Windows desktop Shell opened a matching application window.";
                    for (int wait = 40; Wire.Now < until; wait = Math.Min(wait * 2, 250))
                    {
                        running = FindWindow(entry, request);
                        if (running != null)
                            break;
                        request.Wait((int)Math.Max(0, Math.Min(wait, until - Wire.Now)));
                    }
                    if (running == null)
                        throw new Failure("observation_incomplete",
                                          "The Windows desktop Shell accepted startup, but no matching window was observed.");
                }
                receipt["application"] = running;
                receipt["confirmation"] = "confirmed";
            }
            catch (Failure error)
            {
                receipt["errorCode"] = error.Code;
                receipt["detail"] = error.Message;
            }
            catch (COMException error)
            {
                receipt["errorCode"] = Equals(receipt["dispatch"], "unknown") ? "execution_unknown" : "input_unavailable";
                receipt["detail"] = "Windows desktop Shell startup failed: " + error.Message;
            }
            return receipt;
        }

        object FindWindow(NativeApplicationEntry entry, Request request, bool verifyAbsence = false)
        {
            var rows = Running(entry, request, new List<string>(), verifyAbsence);
            if (rows.Length > 1)
                throw new Failure("ambiguous_target",
                                  "Several Windows application instances match the launch entry. Select a running window.");
            return rows.SingleOrDefault();
        }

        object[] Running(NativeApplicationEntry entry, Request request, List<string> notes, bool verifyAbsence = false)
        {
            var catalogue = store.Catalog(request, notes, accessibility: false);
            var matches = catalogue.Windows.Where(window => Matches(entry, window, request)).ToArray();
            // A verified matching identity stands on its own when unrelated
            // windows omit metadata. The normal app read selects its windows.
            if (matches.Length != 0)
                notes.Clear();
            else if (verifyAbsence && catalogue.NativeProcessNames == null)
                throw new Failure("observation_incomplete",
                                  "Windows could not confirm whether the selected application already has a running window.");
            return NativeReadBackend.ApplicationRows(matches);
        }

        static bool Matches(NativeApplicationEntry entry, NativeWindowRecord window, Request request)
        {
            request.Check();
            if (!NativeWindowDiscovery.Visible(window.Native.Handle))
                return false;
            // A hosted window can declare its own AUMID. Packaged desktop
            // apps such as Paint can instead expose it on the owning process.
            bool matches = entry.ShellId != null && window.ApplicationIdentifier != null
                               ? NativeApplicationShell.Same(entry.ShellId, window.ApplicationIdentifier)
                               : ProcessMatches(entry, window.Native.Pid);
            if (!matches)
                return false;
            try
            {
                window.Native.Validate();
                return true;
            }
            catch (Failure) { return false; }
        }

        [DllImport("kernel32.dll")]
        static extern IntPtr OpenProcess(uint access, bool inherit, uint process);
        [DllImport("kernel32.dll", CharSet = CharSet.Unicode)]
        static extern bool QueryFullProcessImageName(IntPtr process, uint flags, StringBuilder path, ref uint size);
        [DllImport("kernel32.dll", CharSet = CharSet.Unicode, ExactSpelling = true)]
        static extern int GetApplicationUserModelId(IntPtr process, ref uint size, StringBuilder id);
        [DllImport("kernel32.dll")]
        static extern bool CloseHandle(IntPtr process);

        static bool ProcessMatches(NativeApplicationEntry entry, uint pid)
        {
            IntPtr process = OpenProcess(0x1000, false, pid); // PROCESS_QUERY_LIMITED_INFORMATION
            if (process == IntPtr.Zero)
                return false;
            try
            {
                if (entry.ShellId != null)
                {
                    var id = new StringBuilder(entry.ShellId.Length + 1);
                    uint idSize = (uint)id.Capacity;
                    int result = GetApplicationUserModelId(process, ref idSize, id);
                    if (result == 0)
                        return NativeApplicationShell.Same(entry.ShellId, id.ToString());
                    if (result == 122) // ERROR_INSUFFICIENT_BUFFER, so this is a different ID.
                        return false;
                }
                if (entry.Executable == null)
                    return false;
                var path = new StringBuilder(entry.Executable.Length + 1);
                uint size = (uint)path.Capacity;
                return QueryFullProcessImageName(process, 0, path, ref size) &&
                       NativeApplicationShell.Same(entry.Executable, path.ToString());
            }
            finally
            {
                CloseHandle(process);
            }
        }
    }
}
