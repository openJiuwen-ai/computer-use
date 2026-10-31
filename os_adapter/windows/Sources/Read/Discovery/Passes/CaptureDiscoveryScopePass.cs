// Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

namespace ComputerUse.Windows
{
    internal static class CaptureDiscoveryScopePass
    {
        internal const string Effects = "QUERY";
        internal static NativeReadDiscoveryGate Run(NativeReadDiscoveryKind kind, Request request,
                                                    INativeReadDiscoveryProvider provider)
        {
            request.Check();
            return provider.Capture(kind, request);
        }
    }
}
