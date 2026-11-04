// Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

using System;

namespace ComputerUse.Windows
{
    internal sealed class NativeReadChildRoute<T>
        where T : class
    {
        internal readonly bool UseSnapshot, ConflictingEmptyCache;
        internal readonly T First;
        internal NativeReadChildRoute(bool snapshot, T first = null, bool conflict = false)
        {
            UseSnapshot = snapshot;
            First = first;
            ConflictingEmptyCache = conflict;
        }
    }
    internal static class NativeReadChildRoutes
    {
        // QUERY port: zero probes for a nonempty cache; exactly one first-child
        // query otherwise. Choosing the walker never starts another full scan.
        internal static NativeReadChildRoute<T> Resolve<T>(bool cacheSucceeded, T[] cached, Func<T> firstChild)
            where T : class
        {
            if (cacheSucceeded && cached != null && cached.Length != 0)
                return new NativeReadChildRoute<T>(true);
            T first = firstChild();
            return new NativeReadChildRoute<T>(false, first, cacheSucceeded && first != null);
        }
        internal static bool Complete(bool collected, bool conflictingEmptyCache)
        {
            return collected && !conflictingEmptyCache;
        }
    }
}
