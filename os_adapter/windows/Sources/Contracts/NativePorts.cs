using System;
using System.Collections.Generic;
using System.Linq;

namespace ComputerUse.Windows
{
    internal interface INativeRuntime
    {
        string[] Capabilities { get; }

        object Perform(string method, Dictionary<string, object> parameters, Request request);
        void Expire();
        void Close();
    }

    internal interface INativeReadBackend
    {
        void Validate(Dictionary<string, object> target, Request request);
        object Inspect(Dictionary<string, object> target, Dictionary<string, object> query, Request request);
        void Release();
    }

    internal interface INativeReadPreparation
    {
        bool NeedsPreparation(Dictionary<string, object> target);
        void PrepareRead(Dictionary<string, object> target, Request request, bool activate);
    }

    internal interface INativeCaptureBackend
    {
        object Capture(Dictionary<string, object> target, string kind, int maxDimension, Request request);
    }

    internal interface INativeReadEventBackend : IDisposable
    {
    }
}
