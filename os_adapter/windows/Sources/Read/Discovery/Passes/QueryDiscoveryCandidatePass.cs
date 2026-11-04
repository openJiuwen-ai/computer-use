// Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

namespace ComputerUse.Windows
{
    internal static class QueryDiscoveryCandidatePass
    {
        internal const string Effects = "QUERY";
        internal static object Run(NativeReadDiscoveryKind kind, NativeReadDiscoveryStamp stamp, Request request,
                                   INativeReadDiscoveryProvider provider)
        {
            request.Check();
            return provider.Query(kind, stamp, request);
        }
    }
}
