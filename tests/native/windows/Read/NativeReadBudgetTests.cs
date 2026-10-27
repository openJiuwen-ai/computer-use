using System;
using System.Collections.Generic;
using System.Threading;

namespace ComputerUse.Windows
{
    // Runtime contract test: a delayed preparation must consume the same soft
    // deadline as collection. This does not simulate UIA success or Windows
    // activation; actual provider/desktop acceptance is separate.
    internal static class NativeReadBudgetTests
    {
        static int assertions;
        static void Assert(bool value, string detail)
        {
            assertions++;
            if (!value)
                throw new InvalidOperationException(detail);
        }

        sealed class Reader : INativeReadBackend, INativeReadPreparation
        {
            internal double PreparedAt, RemainingAtCollection;
            internal int Delay;
            public void Validate(Dictionary<string, object> target, Request r)
            {
                r.Check();
            }

            public bool NeedsPreparation(Dictionary<string, object> target)
            {
                return true;
            }

            public void PrepareRead(Dictionary<string, object> target, Request r, bool activate)
            {
                PreparedAt = Wire.Now;
                Assert(r.ObservationDeadline <= PreparedAt + Request.ObservationMs, "soft budget was not assigned before preparation");
                if (Delay > 0)
                    r.Wait(Delay);
            }

            public object Inspect(Dictionary<string, object> target, Dictionary<string, object> query, Request r)
            {
                RemainingAtCollection = r.ObservationDeadline - Wire.Now;
                Assert(r.ObservationDeadline <= PreparedAt + Request.ObservationMs, "preparation renewed collection budget");
                Assert(r.ObservationDeadline <= r.Deadline, "soft budget exceeded caller deadline");
                return Wire.Obj("softRemaining", RemainingAtCollection);
            }

            public void Release()
            {
            }
        }

        static Dictionary<string, object> Target(string node)
        {
            return Wire.Obj("session", "s", "instance", "i", "rootWindow", "r", "surface", "w", "node", node, "capture", null);
        }

        static void Main()
        {
            var reader = new Reader
            {
                Delay = 2850
            };
            var runtime = new NativeRuntime("s", reader);
            var opened = Wire.Object(runtime.Perform("begin", Wire.Obj("mode", "read", "target", Target(null), "intent", Wire.Obj("kind", "read", "parameters", Wire.Obj())), new Request(CancellationToken.None, 20000)));
            string operation = (string)opened["operation"];
            var query = Wire.Obj("kind", "nodes", "fields", new[] { "Name" }, "maxNodes", 10, "maxDepth", 1);
            Func<Request, object> read = request => runtime.Perform("inspect", Wire.Obj("operation", operation, "target", Target("n"), "query", query), request);
            read(new Request(CancellationToken.None, 10000));
            Assert(reader.RemainingAtCollection > 0 && reader.RemainingAtCollection < 1400, "slow preparation did not reduce the collection allowance");
            double slowRemaining = reader.RemainingAtCollection;
            reader.Delay = 0;
            read(new Request(CancellationToken.None, 1000));
            Assert(reader.RemainingAtCollection <= 1000, "short caller deadline was extended");
            read(new Request(CancellationToken.None, 10000));
            Assert(reader.RemainingAtCollection > 3500, "a separate command inherited the previous command's spent budget");
            Assert(Request.ObservationMs + 500 <= Request.IsolatedReadMs, "no return/serialization margin before isolation cutoff");
            runtime.Perform("finish", Wire.Obj("operation", operation), new Request(CancellationToken.None, 1000));
            var native = new NativeReadBackend("s");
            try
            {
                var expired = new Request(CancellationToken.None, 1000)
                {
                    ObservationDeadline = Wire.Now - 1
                };
                try
                {
                    native.Inspect(Target("n"), query, expired);
                    throw new InvalidOperationException("expired collection returned an invalid empty node graph");
                }
                catch (Failure error)
                {
                    Assert(error.Code == "observation_unavailable", "spent observation budget became a protocol error");
                }
            }
            finally
            {
                native.Dispose();
            }

            Console.WriteLine(Wire.Json(Wire.Obj("assertions", assertions, "slowPreparationMs", 2850, "collectionRemainingMs", slowRemaining, "scope", "production runtime deadline contract; no provider or GUI claim")));
        }
    }
}
