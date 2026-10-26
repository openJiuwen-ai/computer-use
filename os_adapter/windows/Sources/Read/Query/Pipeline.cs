using System;
using System.Collections.Generic;
using System.Linq;

namespace ComputerUse.Windows
{
    // Native scope checks and observation preparation preserve their original
    // order. This pipeline cannot access NativeRuntime fields or its step log.
    internal sealed class NativeQueryPipeline
    {
        readonly INativeReadBackend reader;
        readonly Func<object, Dictionary<string, object>> binding;
        readonly Func<NativeWriteOperation, Dictionary<string, object>, string[], Request, object> inspectInput;
        readonly Func<NativeWriteOperation, Dictionary<string, object>, Request, object> inspectCurrentReceiver;
        readonly Action finish, expire;
        internal NativeQueryPipeline(INativeReadBackend reader, Func<object, Dictionary<string, object>> binding, Func<NativeWriteOperation, Dictionary<string, object>, string[], Request, object> inspectInput, Action finish, Action expire, Func<NativeWriteOperation, Dictionary<string, object>, Request, object> inspectCurrentReceiver = null)
        {
            this.reader = reader;
            this.binding = binding;
            this.inspectInput = inspectInput;
            this.finish = finish;
            this.expire = expire;
            this.inspectCurrentReceiver = inspectCurrentReceiver;
        }

        internal object Run(Dictionary<string, object> p, Request r, NativeOperationView state)
        {
            var scope = ResolveQueryScopePass.Run(p, binding);
            bool directory = CheckQueryBoundaryPass.Run(scope, state);
            r.Deadline = Math.Min(r.Deadline, state.Deadline);
            try
            {
                r.ObservationDeadline = Math.Min(r.Deadline, Wire.Now + Request.ObservationMs);
                ValidateQueryTargetPass.Run(scope, r, reader);
                CheckQueryOperationPass.Run(scope, state.Write, inspectInput != null);
                if ((string)scope.Query["kind"] != "inputContext")
                    PrepareReadSurfacePass.Run(scope, directory, r, state.Write, reader);
                var result = (string)scope.Query["kind"] == "inputContext" ? scope.Query.ContainsKey("currentReceiver") ? CollectCurrentReceiverPass.Run(scope, r, state.Write, inspectCurrentReceiver) : CollectInputContextPass.Run(scope, r, state.Write, inspectInput) : CollectRequestedFactsPass.Run(scope, r, reader);
                return EncodeNativeFactsPass.Run(result, r);
            }
            catch (Failure error)
            {
                if (error.Code == "cancelled" || error.Code == "deadline_exceeded")
                    finish();
                throw;
            }
            finally
            {
                expire();
            }
        }
    }
}
