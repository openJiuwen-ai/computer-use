using System;
using System.Collections.Generic;
using System.Linq;

namespace ComputerUse.Windows
{
    // One selected addressed UIA unit; last guard/mark/send are adjacent.
    internal sealed class NativeObjectPipeline
    {
        readonly INativeObjectProvider provider;
        internal NativeObjectPipeline(INativeObjectProvider provider)
        {
            this.provider = provider;
        }

        internal object Execute(NativeWriteOperation operation, Dictionary<string, object> command, Request r)
        {
            var target = Wire.Object(command["target"]);
            string unit = Wire.Str(command, "kind"), id = Wire.Str(command, "primitiveId");
            var result = Wire.Obj("target", new Dictionary<string, object>(target), "dispatch", "notDispatched", "detail", "No native input dispatched.", "preparation", new string[0], "confirmation", "unavailable", "cleanup", "complete", "errorCode", null, "retryReason", null, "context", null);
            try
            {
                var plan = ResolveNativeTargetPass.Run(operation, command, target, unit, id);
                var state = ReadInitialTargetPass.Run(plan, r, provider);
                var mutate = PrepareNativeUnitPass.Run(plan, state, r, provider);
                CheckDispatchConditionsPass.Run(plan, r, provider);
                MarkDispatchInFlightPass.Run(operation, r, result);
                SendNativeUnitPass.Run(mutate, result);
                if (operation.ClickV1)
                    operation.Click.Completed();
                result["detail"] = "Native object operation returned; read the control to verify its effect.";
                if (unit == "native_text_edit")
                    ObserveObjectEffectPass.Run(plan, state, r, result, provider);
                r.Check();
            }
            catch (Exception error)
            {
                if (operation.ClickV1 && operation.Click.CallInFlight)
                    operation.Click.Unknown();
                BuildObjectFailurePass.Run(error, operation, result);
            }

            return result;
        }
    }
}
