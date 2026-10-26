using System;
using System.Collections.Generic;
using System.Linq;

namespace ComputerUse.Windows
{
    internal sealed class NativeCapturePipeline
    {
        readonly INativeReadBackend reader;
        readonly INativeCaptureBackend capture;
        readonly Func<object, Dictionary<string, object>> binding;
        readonly Action finish, expire;
        internal NativeCapturePipeline(INativeReadBackend reader, INativeCaptureBackend capture, Func<object, Dictionary<string, object>> binding, Action finish, Action expire)
        {
            this.reader = reader;
            this.capture = capture;
            this.binding = binding;
            this.finish = finish;
            this.expire = expire;
        }

        internal object Run(Dictionary<string, object> p, Request r, NativeOperationView state)
        {
            if (capture == null)
                throw new Failure("mode_unsupported", "this native driver has no capture provider");
            var scope = ResolveCaptureScopePass.Run(p, binding, state);
            r.Deadline = Math.Min(r.Deadline, state.Deadline);
            try
            {
                r.Check();
                PrepareCaptureSurfacePass.Run(scope, r, reader);
                return CollectPixelsPass.Run(scope, r, capture);
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
