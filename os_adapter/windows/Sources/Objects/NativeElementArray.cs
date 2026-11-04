// Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

using System;
using System.Runtime.InteropServices;
using System.Windows.Automation;

namespace ComputerUse.Windows
{
    // A read client can preserve an unavailable position and continue the
    // remaining independently addressable array members. Without an explicit
    // callback the existing strict conversion contract is unchanged.
    internal static class NativeElementArray
    {
        internal static T[] Read<T>(int count, Func<int, T> item, Action<int, Exception> onUnavailable = null)
            where T : class
        {
            var result = new T[count];
            for (int i = 0; i < count; i++)
            {
                if (onUnavailable == null)
                {
                    result[i] = item(i);
                    continue;
                }
                try
                {
                    result[i] = item(i);
                }
                catch (COMException error)
                {
                    onUnavailable(i, error);
                }
                catch (ElementNotAvailableException error)
                {
                    onUnavailable(i, error);
                }
            }
            return result;
        }
    }
}
