// Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

namespace ComputerUse.Windows
{
    internal static class ValidateDiscoveryStabilityPass
    {
        internal const string Effects = "QUERY";
        internal static bool Run(NativeReadDiscoveryKind kind, NativeReadDiscoveryStamp stamp, Request request,
                                 INativeReadDiscoveryProvider provider)
        {
            request.Check();
            return provider.Stable(kind, stamp, request);
        }
    }
}
