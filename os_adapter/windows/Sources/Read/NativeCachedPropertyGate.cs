// Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

using System;

namespace ComputerUse.Windows
{
    // Preserve the existing cached -> current fallback and expose its failed
    // cached result to the owning observation without performing another read.
    internal static class NativeCachedPropertyGate
    {
        internal static object Read(Func<object> cached, Func<object> current, Action<Exception> failed, Action check)
        {
            try
            {
                return cached();
            }
            catch (InvalidOperationException error)
            {
                check();
                if (failed != null)
                    failed(error);
                return current();
            }
        }
    }
}
