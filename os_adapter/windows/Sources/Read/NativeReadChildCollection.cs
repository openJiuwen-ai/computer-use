// Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

using System;
using System.Collections.Generic;

namespace ComputerUse.Windows
{
    internal sealed class NativeReadChildren
    {
        internal readonly IEnumerable<UiaElement> Items;
        internal readonly bool FiniteSnapshot;
        internal readonly bool ConflictingEmptyCache;
        internal NativeReadChildren(IEnumerable<UiaElement> items, bool finiteSnapshot,
                                    bool conflictingEmptyCache = false)
        {
            Items = items;
            FiniteSnapshot = finiteSnapshot;
            ConflictingEmptyCache = conflictingEmptyCache;
        }
    }
    internal sealed class NativeReadChildResult
    {
        internal readonly List<string> Targets;
        internal bool Complete = true;
        internal string Detail;
        internal NativeReadChildResult(List<string> observed = null)
        {
            Targets = observed ?? new List<string>();
        }
    }
    // QUERY-local relation collection. Per-member failures retain later members;
    // a finite cached array can contain a duplicate followed by healthy objects.
    // A walker repeat is a navigation cycle, not an invitation to keep polling.
    internal static class NativeReadChildCollection
    {
        internal static NativeReadChildResult Collect<T>(IEnumerable<T> items, bool finiteSnapshot,
                                                         Func<T, string> retain, Action check,
                                                         Action<Exception> unavailable, int maximum = 4096,
                                                         List<string> observed = null)
        {
            // The relation owner supplies its list so a terminal boundary or
            // soft timeout cannot erase references already observed by it.
            var result = new NativeReadChildResult(observed);
            var seen = new HashSet<string>();
            int visited = 0, duplicates = 0, failed = 0;
            string firstMemberError = null;
            try
            {
                foreach (var item in items)
                {
                    check();
                    // Count visits as well as retained IDs: weak objects and
                    // failed retention must not create an unbounded walker.
                    if (visited++ >= maximum)
                    {
                        result.Complete = false;
                        result.Detail = "Native child enumeration limited to " + maximum +
                                        " positions; remaining membership is unknown.";
                        break;
                    }
                    string id;
                    try
                    {
                        id = retain(item);
                    }
                    catch (Exception error)
                    {
                        unavailable(error);
                        check();
                        result.Complete = false;
                        failed++;
                        if (firstMemberError == null)
                            firstMemberError = error.Message;
                        continue;
                    }
                    if (!seen.Add(id))
                    {
                        result.Complete = false;
                        duplicates++;
                        if (!finiteSnapshot)
                        {
                            result.Detail = "RawViewWalker repeated an object; the navigation cycle was stopped.";
                            break;
                        }
                        continue;
                    }
                    result.Targets.Add(id);
                }
            }
            catch (Exception error)
            {
                unavailable(error);
                check();
                result.Complete = false;
                result.Detail = "Native child navigation failed after the observed members: " + error.Message;
            }
            string members =
                failed == 0
                    ? null
                    : failed +
                          " relation member(s) could not be retained; later members were preserved. First failure: " +
                          firstMemberError;
            string repeated =
                duplicates == 0 ? null : duplicates + " repeated object(s) were omitted from the child relation.";
            result.Detail = String.Join(" ", new[] { result.Detail, members, repeated }).Trim();
            return result;
        }
    }
}
