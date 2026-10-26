using System;
using System.Collections.Generic;
using System.Threading;

namespace ComputerUse.Windows
{
    // Private worker -> owning driver display notifications. Scalar-only,
    // coalesced off the input thread. Never part of the public v5 protocol.
    internal static class AgentPointerFeedback
    {
        static readonly object gate = new object ();
        static object latest;
        static long sequence;
        static bool pending;
        internal static Action<object> Output;
        internal static object Latest
        {
            get
            {
                lock (gate)
                    return latest;
            }
        }

        internal static void Point(string operation, Dictionary<string, object> target, NativePointerPoint point, bool pulse, int count, bool dragging)
        {
            if (Output == null)
                return;
            lock (gate)
            {
                latest = Wire.Obj("operation", operation, "sequence", ++sequence, "target", new Dictionary<string, object>(target), "x", point.X, "y", point.Y, "pulse", pulse, "count", count, "dragging", dragging);
                if (pending)
                    return;
                pending = true;
            }

            ThreadPool.QueueUserWorkItem(unused =>
            {
                while (true)
                {
                    object notice;
                    lock (gate)
                    {
                        notice = latest;
                    }

                    try
                    {
                        var output = Output;
                        if (output != null)
                            output(notice);
                    }
                    catch
                    {
                    }

                    lock (gate)
                    {
                        if (ReferenceEquals(latest, notice))
                        {
                            pending = false;
                            return;
                        }
                    }
                }
            });
        }
    }
}
