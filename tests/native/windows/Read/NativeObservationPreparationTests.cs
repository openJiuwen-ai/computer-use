// Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

using System;
using System.Collections.Generic;
using System.Linq;
using System.Threading;

namespace ComputerUse.Windows
{
    internal static class NativeObservationPreparationTests
    {
        sealed class Reader : INativeReadBackend, INativeReadPreparation, INativeCaptureBackend
        {
            internal int Preparations, Inspections, Captures;
            public void Validate(Dictionary<string, object> target, Request r) { r.Check(); }
            public void Release() { }
            public object Inspect(Dictionary<string, object> target, Dictionary<string, object> query, Request r)
            {
                Inspections++;
                return Wire.Obj("kind", "nodes");
            }
            public object Capture(Dictionary<string, object> target, string kind, int dimension, Request r)
            {
                Captures++;
                return Wire.Obj("kind", "capture");
            }
            public object PrepareObservation(Dictionary<string, object> target, Request r)
            {
                Preparations++;
                r.Effect = true;
                return Wire.Obj("kind", "observationPreparation", "binding", target, "activation", "requested",
                                "raiseStatus", "requested", "foreground", true, "redraw", "unknown");
            }
        }

        static Request Request() { return new Request(CancellationToken.None, 5000); }
        static void Require(bool condition, string message)
        {
            if (!condition)
                throw new InvalidOperationException(message);
        }

        static int Main()
        {
            try { Observe(); return 0; }
            catch (Exception error)
            {
                Console.Error.WriteLine(error.GetType().Name + ": " + error.Message);
                return 1;
            }
        }

        static void Observe()
        {
            var reader = new Reader();
            var runtime = new NativeRuntime("session", reader, capture: reader);
            var target = Wire.Obj("session", "session", "instance", "instance", "rootWindow", "window",
                                  "surface", "surface", "node", null, "capture", null);
            var begun = Wire.Object(runtime.Perform("begin", Wire.Obj("mode", "read", "target", target,
                                    "intent", Wire.Obj("kind", "read", "parameters", Wire.Obj())), Request()));
            var token = begun["operation"];
            var node = new Dictionary<string, object>(target);
            node["node"] = "node";
            var query = Wire.Obj("kind", "nodes", "maxNodes", 1, "maxDepth", 0, "fields", new[] { "Name" });
            var inspect = Request();
            runtime.Perform("inspect", Wire.Obj("operation", token, "target", node, "query", query), inspect);
            var capture = Request();
            runtime.Perform("capture", Wire.Obj("operation", token, "target", target, "kind", "window",
                                                "maxDimension", 100), capture);
            Require(reader.Preparations == 0 && !inspect.Effect && !capture.Effect,
                    "ordinary inspect/capture must not prepare the foreground");
            Require(reader.Inspections == 1 && reader.Captures == 1, "ordinary observations still collect facts and pixels");
            Require(runtime.Capabilities.Contains("nativeObservationPreparation"), "the prepared route is negotiated");

            var prepare = Request();
            var receipt = Wire.Object(runtime.Perform("prepareObservation",
                Wire.Obj("operation", token, "target", target), prepare));
            Require(reader.Preparations == 1 && prepare.Effect && !prepare.Dispatched,
                    "only explicit preparation can change the foreground; it is not content input");
            Require(Equals(receipt["foreground"], true) && Equals(receipt["redraw"], "unknown"),
                    "the observation receipt must distinguish foreground from redraw");

            var other = new Dictionary<string, object>(target);
            other["surface"] = "other";
            try
            {
                runtime.Perform("prepareObservation", Wire.Obj("operation", token, "target", other), Request());
                throw new InvalidOperationException("preparation must not change the original read scope");
            }
            catch (Failure error)
            {
                Require(error.Code == "invalid_request" && reader.Preparations == 1,
                        "scope mismatch must be rejected before preparation");
            }
            runtime.Perform("finish", Wire.Obj("operation", token), Request());
            runtime.Close();
            Console.WriteLine("Windows observation preparation tests passed");
        }
    }
}
