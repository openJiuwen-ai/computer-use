// Isolated stage/extension probes. No NativeRuntime, UIA or Windows desktop.
using System;
using System.Collections.Generic;
using System.Linq;
using System.Threading;
using System.Text.Json;

namespace ComputerUse.Windows
{
    internal static class NativePipelineStagesTests
    {
        static int assertions;
        static void Assert(bool condition, string reason)
        {
            assertions++;
            if (!condition)
                throw new InvalidOperationException(reason);
        }

        static Request Request()
        {
            return new Request(CancellationToken.None, 10000);
        }

        static Dictionary<string, object> Target()
        {
            return Wire.Obj("session", "session", "instance", "instance", "rootWindow", "window", "surface", "surface", "node", "node", "capture", null);
        }

        static void Refused(Action action)
        {
            try
            {
                action();
            }
            catch (Failure)
            {
                assertions++;
                return;
            }

            throw new InvalidOperationException("expected a native refusal");
        }

        static void StageBoundaries()
        {
            var target = Target();
            var operation = new NativeWriteOperation(target, Wire.Obj("kind", "click", "parameters", Wire.Obj()));
            var contract = new NativeInputContract
            {
                Id = "one",
                Target = target,
                Within = new string[0],
                Unit = "native_action",
                Action = "Invoke.Invoke",
                Role = "ControlType.Edit"
            };
            operation.Contracts.Add(contract.Id, contract);
            var command = Wire.Obj("kind", "native_action", "primitiveId", "one", "target", target, "foreground", false);
            var provider = new ObjectProbe();
            var plan = ResolveNativeTargetPass.Run(operation, command, target, "native_action", "one");
            Assert(provider.Reads == 0 && provider.Sent == 0, "target resolution queried a provider");
            var initial = ReadInitialTargetPass.Run(plan, Request(), provider);
            var mutate = PrepareNativeUnitPass.Run(plan, initial, Request(), provider);
            provider.Enabled = false;
            Refused(() => CheckDispatchConditionsPass.Run(plan, Request(), provider));
            Assert(provider.Sent == 0, "last guard failure dispatched an object action");
            provider.Enabled = true;
            CheckDispatchConditionsPass.Run(plan, Request(), provider);
            var r = Request();
            var result = Wire.Obj("dispatch", "notDispatched");
            MarkDispatchInFlightPass.Run(operation, r, result);
            Assert(operation.Dispatched && r.Dispatched && (string)result["dispatch"] == "unknown", "in-flight barrier was late");
            SendNativeUnitPass.Run(mutate, result);
            Assert(provider.Sent == 1 && (string)result["dispatch"] == "dispatched", "single addressed send changed");
        }

        static void UncertainDispatch()
        {
            var target = Target();
            var provider = new ObjectProbe();
            var operation = new NativeWriteOperation(target, Wire.Obj("kind", "click", "parameters", Wire.Obj()));
            operation.Contracts.Add("one", new NativeInputContract { Id = "one", Target = target, Within = new string[0], Unit = "native_action", Action = "Invoke.Invoke", Role = "ControlType.Edit" });
            provider.OnSend = () =>
            {
                throw new InvalidOperationException("lost provider reply");
            };
            var result = Wire.Object(new NativeObjectPipeline(provider).Execute(operation, Wire.Obj("kind", "native_action", "primitiveId", "one", "target", target, "foreground", false), Request()));
            Assert(provider.Sent == 1 && operation.Dispatched && (string)result["dispatch"] == "unknown", "uncertain native call replayed or lost its barrier");
            Assert((string)result["errorCode"] == "execution_unknown" && result["retryReason"] == null, "uncertain send authorized another route");
        }

        static void LocalExtension()
        {
            var target = Target();
            var provider = new ObjectProbe();
            var contract = new NativeInputContract
            {
                Target = target,
                Within = new string[0],
                Unit = "native_action",
                Action = "Invoke.Invoke",
                Role = "ControlType.Edit"
            };
            var plan = new ObjectDispatchPlan(contract, target, "native_action", null);
            // One extra pure rule in a test-only composition, followed by the
            // existing query stage. No provider, registry or Owner is copied.
            Func<bool, NativeObjectState> extension = permitted =>
            {
                if (!permitted)
                    throw new Failure("input_unavailable", "extension refused before querying");
                return ReadInitialTargetPass.Run(plan, Request(), provider);
            };
            Refused(() => extension(false));
            Assert(provider.Reads == 0, "refused extension entered the native sibling");
            Assert(extension(true).Enabled == true && provider.Reads == 1, "extension did not reuse the original native provider");
            var state = new NativeOperationView("one", target, null, Wire.Now + 1000);
            var scope = new NativeQueryRequest("other", target, Wire.Obj("kind", "nodes"));
            Refused(() => CheckQueryBoundaryPass.Run(scope, state));
            Assert(provider.Reads == 1 && provider.Sent == 0, "query boundary refusal affected another mechanism");
        }

        public static int Main()
        {
            StageBoundaries();
            UncertainDispatch();
            LocalExtension();
            Console.WriteLine(JsonSerializer.Serialize(new { assertions = assertions, scope = "Isolated native stage contracts and local extension; no Windows desktop" }));
            return 0;
        }
    }
}
