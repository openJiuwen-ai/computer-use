using System;
using System.Collections.Generic;
using System.Windows.Automation;

namespace ComputerUse.Windows
{
    // Discovery returns native handles, not UIA objects or input permission.
    // Optional desktop UIA metadata has its own windowless MTA process. Its
    // timeout must not retire the process holding a selected window's objects.
    // Only scalars cross this boundary; their native lifetimes are rechecked.
    internal static class NativeWindowDiscovery
    {
        internal sealed class Candidate
        {
            internal IntPtr Handle;
            internal uint Pid;
            internal long Generation;
        }

        internal sealed class Snapshot
        {
            internal readonly List<Candidate> Windows = new List<Candidate>();
            internal readonly List<string> Notes = new List<string>();
        }

        static readonly object gate = new object ();
        static NativeChildProcess supplement;
        static long sequence;
        static bool unavailableForLease;
        internal static bool RequiresRestart { get; private set; }

        internal static bool Visible(IntPtr handle)
        {
            if (handle == IntPtr.Zero || !Native.IsWindow(handle) || !Native.IsWindowVisible(handle) || AgentPointerWindow.IsDecoration(handle))
                return false;
            int cloaked;
            return Native.DwmGetWindowAttribute(handle, 14, out cloaked, 4) != 0 || cloaked == 0;
        }

        static Snapshot Uia()
        {
            var result = new Snapshot();
            try
            {
                var desktop = UiaElement.RootElement;
                var cache = UiaClient.Cache(new[] { AutomationElement.NativeWindowHandleProperty.Id, AutomationElement.ProcessIdProperty.Id }, true);
                {
                    var children = desktop.Snapshot(cache).CachedChildren;
                    if (children.Length > 512)
                        result.Notes.Add("UIA desktop directory limited to 512 entries.");
                    for (int n = 0; n < Math.Min(children.Length, 512); n++)
                    {
                        try
                        {
                            var cached = children[n];
                            var h = new IntPtr(Convert.ToInt64(cached.GetCachedPropertyValue(AutomationElement.NativeWindowHandleProperty)));
                            if (h == IntPtr.Zero)
                            {
                                NativeReadStore.Note(result.Notes, "A UIA desktop entry has no native window handle; it cannot yet be selected as a working window.");
                                continue;
                            }

                            if (!Visible(h))
                                continue;
                            // Do not map an unrelated provider to its host by a
                            // name/rectangle heuristic. HWND + live PID agree.
                            uint pid = Native.PID(h);
                            var reportedPid = Convert.ToInt32(cached.GetCachedPropertyValue(AutomationElement.ProcessIdProperty));
                            if (pid == 0 || reportedPid <= 0 || pid != (uint)reportedPid)
                            {
                                NativeReadStore.Note(result.Notes, "A UIA desktop entry has conflicting native process identity.");
                                continue;
                            }

                            if (Native.GetAncestor(h, 2) != h)
                                continue;
                            result.Windows.Add(new Candidate { Handle = h, Pid = pid, Generation = Native.Generation(h) });
                        }
                        catch (Exception error)
                        {
                            NativeReadStore.Note(result.Notes, "UIA desktop entry unavailable: " + error.Message);
                        }
                    }
                }
            }
            catch (Exception error)
            {
                NativeReadStore.Note(result.Notes, "UIA desktop enumeration unavailable: " + error.Message);
            }

            return result;
        }

        internal static long WorkerRequest(string line)
        {
            var frame = Wire.Object(Wire.Serializer().DeserializeObject(line));
            Wire.Keys(frame, "id");
            return (long)Wire.Num(frame, "id", 1, 9007199254740991, true);
        }

        internal static Snapshot WorkerReply(string line, long expected, Dictionary<IntPtr, long> before)
        {
            var frame = Wire.Object(Wire.Serializer().DeserializeObject(line));
            Wire.Keys(frame, "id,windows,notes");
            if ((long)Wire.Num(frame, "id", 1, 9007199254740991, true) != expected)
                throw new InvalidOperationException("directory worker response identity mismatch");
            var rows = Wire.Array(frame["windows"]);
            var messages = Wire.Array(frame["notes"]);
            if (rows.Length > 512 || messages.Length > 128)
                throw new InvalidOperationException("directory worker response exceeded its metadata budget");
            var result = new Snapshot();
            foreach (var row in rows)
            {
                var item = Wire.Object(row);
                Wire.Keys(item, "handle,pid");
                var h = new IntPtr((long)Wire.Num(item, "handle", -9007199254740991, 9007199254740991, true));
                if (h == IntPtr.Zero)
                    throw new InvalidOperationException("directory worker returned no native handle");
                uint pid = (uint)Wire.Num(item, "pid", 1, UInt32.MaxValue, true);
                long generation;
                before.TryGetValue(h, out generation);
                result.Windows.Add(new Candidate { Handle = h, Pid = pid, Generation = generation });
            }

            foreach (var message in messages)
            {
                var text = message as string;
                if (text == null || text.Length > 4096)
                    throw new InvalidOperationException("directory worker returned an invalid note");
                result.Notes.Add(text);
            }

            return result;
        }

        internal static int RunWorker()
        {
            // Program's MTA entry point reaches this before window/event/input
            // initialization. This private protocol has no action command.
            string line;
            while ((line = Console.ReadLine()) != null)
            {
                long id = WorkerRequest(line);
                var value = Uia();
                Console.WriteLine(Wire.Json(Wire.Obj("id", id, "windows", value.Windows.ConvertAll(item => Wire.Obj("handle", item.Handle.ToInt64(), "pid", item.Pid)).ToArray(), "notes", value.Notes)));
                Console.Out.Flush();
            }

            return 0;
        }

        static bool StopSupplement()
        {
            if (supplement == null)
                return true;
            if (!supplement.Stop())
            {
                RequiresRestart = true;
                return false;
            }

            supplement.Dispose();
            supplement = null;
            return true;
        }

        internal static void Release()
        {
            lock (gate)
            {
                unavailableForLease = false;
            }
        }

        internal static void Close()
        {
            lock (gate)
            {
                if (!StopSupplement())
                    throw new Failure("observation_unavailable", "directory worker cleanup could not be confirmed");
            }
        }

        static Snapshot Supplement(Request request, List<string> notes)
        {
            lock (gate)
            {
                if (RequiresRestart || unavailableForLease)
                {
                    NativeReadStore.Note(notes, "UIA desktop metadata supplement is unavailable in this read lease; using native window discovery without repeating it.");
                    return null;
                }

                var before = Native.GenerationSnapshot();
                double until = Math.Min(request.Deadline, Wire.Now + 1200);
                try
                {
                    request.Check();
                    if (supplement == null)
                        supplement = new NativeChildProcess("--window-directory-worker");
                    long id = ++sequence;
                    supplement.Send(Wire.Obj("id", id));
                    while (Wire.Now < until)
                    {
                        request.Check();
                        string line = supplement.Poll(20);
                        if (line != null)
                        {
                            request.Check();
                            return WorkerReply(line, id, before);
                        }
                    }

                    throw new NativeFieldUnavailable("timeout", "UIA desktop enumeration exceeded its isolated 1200 ms budget");
                }
                catch (Exception error)
                {
                    unavailableForLease = true;
                    StopSupplement();
                    request.Check();
                    NativeReadStore.Note(notes, "UIA desktop enumeration unavailable: " + error.Message + "; native window identities and independent target root collection remain available.");
                    return null;
                }
            }
        }

        internal static List<IntPtr> Collect(Request request, List<string> notes, bool accessibility = true)
        {
            request.Check();
            var handles = new List<IntPtr>();
            if (!Native.EnumWindows((h, p) =>
            {
                if (Visible(h))
                    handles.Add(h);
                return true;
            }, IntPtr.Zero))
                NativeReadStore.Note(notes, "Win32 window enumeration failed; directory coverage is incomplete.");
            var seen = new HashSet<IntPtr>(handles);
            // Foreground is an independent discovery entry; it
            // is evidence of an interface, not a request to activate anything.
            IntPtr front = Native.GetForegroundWindow();
            IntPtr root = front == IntPtr.Zero ? IntPtr.Zero : Native.GetAncestor(front, 2);
            if (Visible(root) && seen.Add(root))
                handles.Insert(0, root);
            var extra = accessibility ? Supplement(request, notes) : null;
            if (extra != null)
            {
                foreach (string note in extra.Notes)
                    NativeReadStore.Note(notes, note);
                foreach (var item in extra.Windows)
                {
                    request.Check();
                    if (Native.Generation(item.Handle) != item.Generation || Native.PID(item.Handle) != item.Pid || !Visible(item.Handle) || Native.GetAncestor(item.Handle, 2) != item.Handle)
                    {
                        NativeReadStore.Note(notes, "A UIA-discovered window changed before directory assembly.");
                        continue;
                    }

                    if (seen.Add(item.Handle))
                        handles.Add(item.Handle);
                }
            }

            return handles;
        }
    }
}
