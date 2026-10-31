// Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

using System;
using System.Collections.Generic;
using System.Linq;
using System.Windows.Automation;
using UIA = NativeUIA;

namespace ComputerUse.Windows
{
    // One inspect, one cache. Full references keep execution possible; the
    // retained objects never acquire a session-long snapshot of property values.
    internal sealed class NativeReadCache
    {
        readonly UIA.IUIAutomationCacheRequest elementCache;
        readonly HashSet<int> cachedProperties;
        readonly Action check;
        readonly Action<string> note;
        readonly Action<string, int, Exception> failedCachedRead;
        readonly Dictionary<string, UiaElement> snapshots = new Dictionary<string, UiaElement>();
        readonly HashSet<string> uncached = new HashSet<string>();
        readonly Dictionary<string, UiaClient.Pattern[]> patterns = new Dictionary<string, UiaClient.Pattern[]>();
        readonly NativeReadObjectKeys<UiaElement> keys;
        string Key(UiaElement e)
        {
            return keys.Get(e);
        }
        void UnavailableArrayMember(int index, Exception error)
        {
            NativeReadBackend.Status(error);
            check();
            note("UIA read array member " + index +
                 " unavailable; its position remains a gap and later members are preserved: " + error.Message);
        }

        internal NativeReadCache(IEnumerable<string> fields, Action check, Action<string> note,
                                 Action<string, int, Exception> failedCachedRead = null)
        {
            this.check = check;
            this.note = note;
            this.failedCachedRead = failedCachedRead;
            keys = new NativeReadObjectKeys<UiaElement>(
                NativeReadStore.Runtime, () => NativeReadStore.Token("unidentified"), check,
                error =>
                {
                    NativeReadBackend.Status(error);
                    check();
                    note(
                        "UIA runtime identity unavailable; this object's properties retain an observation-local cache key: " +
                        error.Message);
                },
                (first, second) =>
                    UiaClient.Call(() => UiaClient.Current.CompareElements(first.Native, second.Native)) != 0,
                note);
            var requested = new HashSet<int> { AutomationElement.RuntimeIdProperty.Id,
                                               AutomationElement.NativeWindowHandleProperty.Id,
                                               AutomationElement.IsPasswordProperty.Id };
            foreach (string name in fields)
            {
                AutomationProperty p;
                if (NativeReadSchema.Properties.TryGetValue(name, out p))
                    requested.Add(p.Id);
                int id;
                if (NativeReadSchema.ReadingProperties.TryGetValue(name, out id))
                    requested.Add(id);
            }

            cachedProperties = requested;
            elementCache = UiaClient.Cache(requested.Concat(UiaClient.Patterns.Select(p => p.Available)));
        }

        internal UiaElement Snapshot(UiaElement element)
        {
            check();
            string id = Key(element);
            UiaElement result;
            if (snapshots.TryGetValue(id, out result))
                return result;
            try
            {
                check();
                result = element.Snapshot(elementCache);
                check();
            }
            catch (Exception error)
            {
                check();
                result = element;
                uncached.Add(id);
                note("UIA property cache unavailable; bounded individual property reads used: " + error.Message);
            }

            snapshots[id] = result;
            return result;
        }

        internal object Get(UiaElement element, AutomationProperty property)
        {
            return Get(element, property.Id);
        }

        internal object Get(UiaElement element, int property)
        {
            var snapshot = Snapshot(element);
            check();
            object value;
            // Relations are not inserted into the property cache: their native
            // objects and scope must be handled explicitly by the collector.
            if (uncached.Contains(Key(element)) || !cachedProperties.Contains(property))
                value = element.GetCurrentPropertyValue(property, true, UnavailableArrayMember);
            else
            {
                value = NativeCachedPropertyGate.Read(
                    () => snapshot.GetCachedPropertyValue(property, true, UnavailableArrayMember),
                    () => element.GetCurrentPropertyValue(property, true, UnavailableArrayMember),
                    error =>
                    {
                        if (failedCachedRead != null)
                            failedCachedRead(Key(element), property, error);
                    },
                    check);
            }

            check();
            if (ReferenceEquals(value, AutomationElement.NotSupported) &&
                property != AutomationElement.NativeWindowHandleProperty.Id)
                throw new NativeFieldUnavailable("unsupported");
            return value;
        }

        internal UiaClient.Pattern[] Patterns(UiaElement element)
        {
            check();
            string id = Key(element);
            UiaClient.Pattern[] result;
            if (patterns.TryGetValue(id, out result))
                return result;
            var snapshot = Snapshot(element);
            result = snapshot.GetSupportedPatterns(!uncached.Contains(id));
            check();
            patterns[id] = result;
            return result;
        }
        internal NativeReadChildren Children(UiaElement element)
        {
            UiaElement[] children = null;
            bool fetched = false;
            try
            {
                check();
                var cache = elementCache.Clone();
                cache.TreeScope = (UIA.TreeScope)2;
                children = element.Snapshot(cache).ReadCachedChildren(UnavailableArrayMember);
                check();
                fetched = true;
            }
            catch (Exception error)
            {
                check();
                note("UIA children cache unavailable; bounded RawViewWalker used: " + error.Message);
            }
            // An empty cache is not evidence of provider consistency. Probe
            // only the first Raw View child; select the walker only when this
            // supplies conflicting evidence, never perform two full scans.
            var route = NativeReadChildRoutes.Resolve(fetched, children,
                                                      () =>
                                                      {
                                                          check();
                                                          var first = UiaClient.FirstChild(element);
                                                          check();
                                                          return first;
                                                      });
            if (route.UseSnapshot)
                return new NativeReadChildren(CachedChildren(children), true);
            if (route.ConflictingEmptyCache)
                note(
                    "UIA cached children were empty but RawViewWalker exposed a child; the walker relation is sampled separately and cache completeness is unknown.");
            return new NativeReadChildren(WalkChildren(route.First), false, route.ConflictingEmptyCache);
        }
        IEnumerable<UiaElement> CachedChildren(UiaElement[] children)
        {
            for (int i = 0; i < children.Length; i++)
            {
                check();
                var child = children[i];
                // A null/stale member is handled by the relation collector;
                // its position must not prevent later cached objects arriving.
                if (child != null)
                {
                    string id = Key(child);
                    if (!snapshots.ContainsKey(id))
                        snapshots[id] = child;
                }
                yield return child;
            }
        }
        IEnumerable<UiaElement> WalkChildren(UiaElement current)
        {
            while (current != null)
            {
                check();
                yield return current;
                check();
                current = UiaClient.NextSibling(current);
            }
        }
    }
}
