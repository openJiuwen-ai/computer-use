// Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

using System;
using System.Collections.Generic;
using System.Runtime.CompilerServices;

namespace ComputerUse.Windows
{
    // Observation-local cache identity only. A missing/failed RuntimeId never
    // grants a native binding and must not prevent a sibling being delivered.
    internal sealed class NativeReadObjectKeys<T>
        where T : class
    {
        sealed class ByReference : IEqualityComparer<T>
        {
            public bool Equals(T a, T b)
            {
                return Object.ReferenceEquals(a, b);
            }
            public int GetHashCode(T value)
            {
                return RuntimeHelpers.GetHashCode(value);
            }
        }
        readonly Dictionary<T, string> keys = new Dictionary<T, string>(new ByReference());
        readonly Dictionary<string, T> owners = new Dictionary<string, T>();
        readonly Func<T, string> runtime;
        readonly Func<string> unidentified;
        readonly Func<T, T, bool> same;
        readonly Action check;
        readonly Action<Exception> unavailable;
        readonly Action<string> conflict;
        internal NativeReadObjectKeys(Func<T, string> runtime, Func<string> unidentified, Action check,
                                      Action<Exception> unavailable, Func<T, T, bool> same = null,
                                      Action<string> conflict = null)
        {
            this.runtime = runtime;
            this.unidentified = unidentified;
            this.check = check;
            this.unavailable = unavailable;
            this.same = same;
            this.conflict = conflict;
        }
        internal string Get(T value)
        {
            string key;
            if (keys.TryGetValue(value, out key))
                return key;
            check();
            try
            {
                key = runtime(value);
            }
            catch (Exception error)
            {
                // The caller classifies terminal cancellation/deadline errors;
                // only this object's observation cache falls back to weak ID.
                unavailable(error);
                check();
                key = null;
            }
            if (String.IsNullOrEmpty(key))
                key = unidentified();
            else
            {
                T owner;
                if (owners.TryGetValue(key, out owner))
                {
                    // A provider RuntimeId alone cannot prove that two native
                    // proxies represent the same object. Preserve the first
                    // cache and isolate the unproved object instead of giving
                    // it somebody else's properties or pattern availability.
                    bool equal = Object.ReferenceEquals(owner, value);
                    if (!equal && same != null)
                    {
                        check();
                        try
                        {
                            equal = same(owner, value);
                            check();
                        }
                        catch (Exception error)
                        {
                            unavailable(error);
                            check();
                        }
                    }
                    if (!equal)
                    {
                        if (conflict != null)
                            conflict(
                                "UIA objects reported the same RuntimeId but equivalence was not confirmed; their property caches remain separate.");
                        key = unidentified();
                    }
                }
                else
                    owners.Add(key, value);
            }
            keys[value] = key;
            return key;
        }
    }
}
