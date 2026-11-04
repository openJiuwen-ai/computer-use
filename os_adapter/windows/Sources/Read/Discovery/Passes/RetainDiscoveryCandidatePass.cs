// Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

namespace ComputerUse.Windows
{
    internal static class RetainDiscoveryCandidatePass
    {
        internal const string Effects = "QUERY/RESOURCE";
        internal static NativeReadDiscoveryRetention Run(NativeReadDiscoveryKind kind, NativeReadDiscoveryStamp stamp,
                                                         object element, Request request,
                                                         INativeReadDiscoveryProvider provider)
        {
            request.Check();
            return provider.Retain(kind, stamp, element, request);
        }
    }
}
