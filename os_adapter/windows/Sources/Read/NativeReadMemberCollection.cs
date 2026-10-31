// Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

using System;
using System.Collections.Generic;

namespace ComputerUse.Windows
{
    // QUERY indexed membership: a position is independently addressable (e.g.
    // Grid.GetItem), so one failed position does not prevent the next one.
    // Repeated IDs are valid positional membership and remain ordered.
    internal static class NativeReadMemberCollection
    {
        internal static NativeReadChildResult CollectPositions(int count, Func<int, string> retain, Action check,
                                                               Action<Exception> unavailable, List<string> observed)
        {
            var result = new NativeReadChildResult(observed);
            int failed = 0;
            string firstError = null;
            for (int index = 0; index < count; index++)
            {
                check();
                try
                {
                    result.Targets.Add(retain(index));
                }
                catch (Exception error)
                {
                    unavailable(error);
                    check();
                    result.Complete = false;
                    failed++;
                    if (firstError == null)
                        firstError = error.Message;
                }
            }
            if (failed != 0)
                result.Detail =
                    failed +
                    " independently addressable relation position(s) failed; later positions were preserved. First failure: " +
                    firstError;
            return result;
        }
    }
}
