using System;
using System.Linq;
using System.Threading;

namespace ComputerUse.Windows
{
    internal static class NativeReadEventsTests
    {
        static int assertions;
        static void Assert(bool value, string detail)
        {
            assertions++;
            if (!value)
                throw new InvalidOperationException(detail);
        }

        [MTAThread]
        static void Main()
        {
            var listener = new NativeReadEvents.Listener();
            string initial = listener.Token;
            Assert(!listener.ChangedSince(initial), "new listener reported a change");
            Assert(!listener.Owns("other:0") && !listener.Owns(initial.Split(':')[0] + ":999999"), "foreign/future epoch accepted");
            listener.HandleAutomationEvent(null, 20015);
            listener.HandleAutomationEvent(null, 20014);
            listener.HandlePropertyChangedEvent(null, 30008, true);
            listener.HandlePropertyChangedEvent(null, 30005, "name");
            listener.HandleStructureChangedEvent(null, (NativeUIA.StructureChangeType)2, new int[0]);
            Assert(listener.ChangedSince(initial), "event failed to invalidate token");
            Assert(listener.KindsSince(initial).OrderBy(s => s).SequenceEqual(new[] { "focus", "property", "selection", "structure", "text" }), "callback kinds lost");
            string stormStart = listener.Token;
            var threads = Enumerable.Range(0, 4).Select(_ => new Thread(() =>
            {
                for (int i = 0; i < 1000; i++)
                    listener.HandlePropertyChangedEvent(null, 30005, "ignored");
            })).ToArray();
            foreach (var thread in threads)
                thread.Start();
            foreach (var thread in threads)
                thread.Join();
            Assert(listener.KindsSince(stormStart).SequenceEqual(new[] { "property" }), "storm created an unbounded callback queue");
            string stopped = listener.Token;
            listener.Stop();
            listener.HandleAutomationEvent(null, 20015);
            listener.HandlePropertyChangedEvent(null, 30008, false);
            listener.HandleStructureChangedEvent(null, (NativeUIA.StructureChangeType)2, null);
            Assert(listener.Token == stopped && !listener.ChangedSince(stopped), "late callback changed a retired listener");
            var replacement = new NativeReadEvents.Listener();
            Assert(!replacement.Owns(stopped), "new subscription reused a retired epoch");
            replacement.Stop();
            Console.WriteLine(Wire.Json(Wire.Obj("assertions", assertions, "stormCallbacks", 4000, "lateCallbacksIgnored", true, "foreignEpochRejected", true, "scope", "production callback state; no UIA provider or desktop input")));
        }
    }
}
