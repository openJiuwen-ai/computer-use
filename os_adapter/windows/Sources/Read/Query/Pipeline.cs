// Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

using System;
using System.Collections.Generic;
using System.Linq;

namespace ComputerUse.Windows
{
    // Native scope checks and fact collection do not change foreground state.
    internal sealed class NativeQueryPipeline
    {
        readonly INativeReadBackend reader;
        readonly Func<object, Dictionary<string, object>> binding;
        readonly Func<NativeWriteOperation, Dictionary<string, object>, string[], Request, object> inspectInput;
        readonly Func<NativeWriteOperation, Dictionary<string, object>, Request, object> inspectCurrentReceiver;
        readonly NativeApplicationLaunch launch;
        readonly Action finish, expire;
        internal NativeQueryPipeline(
            INativeReadBackend reader, Func<object, Dictionary<string, object>> binding,
            Func<NativeWriteOperation, Dictionary<string, object>, string[], Request, object> inspectInput,
            Action finish, Action expire,
            Func<NativeWriteOperation, Dictionary<string, object>, Request, object> inspectCurrentReceiver = null,
            NativeApplicationLaunch launch = null)
        {
            this.reader = reader;
            this.binding = binding;
            this.inspectInput = inspectInput;
            this.finish = finish;
            this.expire = expire;
            this.inspectCurrentReceiver = inspectCurrentReceiver;
            this.launch = launch;
        }

        internal object Run(Dictionary<string, object> p, Request r, NativeOperationView state)
        {
            var scope = ResolveQueryScopePass.Run(p, binding);
            CheckQueryBoundaryPass.Run(scope, state);
            r.Deadline = Math.Min(r.Deadline, state.Deadline);
            bool cleanupFailure = false;
            try
            {
                r.ObservationDeadline = Math.Min(r.Deadline, Wire.Now + Request.ObservationMs);
                bool launchContract = (string)scope.Query["kind"] == "launch";
                bool launchLookup = scope.Query.ContainsKey("launchSelector") || scope.Query.ContainsKey("installation");
                if ((launchContract || launchLookup) && launch == null)
                    throw new Failure("unsupported_operation", "this native driver has no Windows launch provider");
                if (launchContract)
                    return EncodeNativeFactsPass.Run(launch.Inspect(state.Write, scope.Target, r), r);
                ValidateQueryTargetPass.Run(scope, r, reader);
                CheckQueryOperationPass.Run(scope, state.Write, inspectInput != null);
                var result = launchLookup ? launch.Lookup(scope.Query, r)
                             : (string)scope.Query["kind"] == "inputContext"
                                 ? scope.Query.ContainsKey("currentReceiver")
                                       ? CollectCurrentReceiverPass.Run(scope, r, state.Write, inspectCurrentReceiver)
                                       : CollectInputContextPass.Run(scope, r, state.Write, inspectInput)
                                 : CollectRequestedFactsPass.Run(scope, r, reader);
                return EncodeNativeFactsPass.Run(result, r);
            }
            catch (NativeDesktopCleanupFailure)
            {
                cleanupFailure = true;
                throw;
            }
            catch (Failure error)
            {
                if (error.Code == "cancelled" || error.Code == "deadline_exceeded")
                    finish();
                throw;
            }
            // This call-local flag only carries an exception through unwind
            // to Perform's existing health owner; it grants no future state.
            finally
            {
                if (!cleanupFailure)
                    expire();
            }
        }
    }
}
