// Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

using System;
using System.Collections.Generic;

namespace ComputerUse.Windows
{
    internal static class CurrentReceiverPath
    {
        internal static NativeCurrentReceiverEvidence Read(object scope, string token, INativeCurrentReceiverTree tree, Request r, Action validateScope)
        {
            // Original-target failures are never classified as optional focus
            // failures. The same target is checked again after provider calls.
            validateScope();
            r.Check();
            NativeCurrentReceiverEvidence evidence;
            try
            {
                evidence = Collect(scope, token, tree, r);
            }
            catch (Failure error)
            {
                if (error.Code != "stale_target" && error.Code != "observation_incomplete" && error.Code != "input_unavailable")
                    throw;
                r.Check();
                evidence = new NativeCurrentReceiverEvidence("unavailable", detail: "Current focus evidence is unavailable: " + error.Message);
            }

            validateScope();
            r.Check();
            return evidence;
        }

        static NativeCurrentReceiverEvidence Collect(object scope, string token, INativeCurrentReceiverTree tree, Request r)
        {
            string scopeIdentity = tree.Identity(scope, r);
            var focused = tree.Focused(r);
            if (focused == null)
                return new NativeCurrentReceiverEvidence("absent", detail: "UIA reports no current keyboard receiver.");
            string focusedIdentity = tree.Identity(focused, r);
            if (String.IsNullOrEmpty(scopeIdentity) || String.IsNullOrEmpty(focusedIdentity))
                return new NativeCurrentReceiverEvidence("unavailable", detail: "The scope or focus has no runtime identity.");
            var path = new List<object>();
            var seen = new HashSet<string>
            {
                focusedIdentity
            };
            var current = focused;
            string identity = focusedIdentity;
            while (identity != scopeIdentity)
            {
                if (path.Count == 128)
                    return new NativeCurrentReceiverEvidence("unavailable", detail: "The current receiver parent path exceeds 128 ancestors.");
                r.Check();
                current = tree.Parent(current, r);
                r.Check();
                if (current == null)
                    return new NativeCurrentReceiverEvidence("conflict", detail: "The current keyboard receiver is outside the requested container.");
                identity = tree.Identity(current, r);
                if (String.IsNullOrEmpty(identity))
                    return new NativeCurrentReceiverEvidence("unavailable", detail: "A current receiver ancestor has no runtime identity.");
                if (!seen.Add(identity))
                    return new NativeCurrentReceiverEvidence("conflict", detail: "The current receiver parent path is cyclic.");
                path.Add(current);
            }

            var ancestors = new List<string>();
            for (int i = 0; i < path.Count; i++)
                ancestors.Add(i == path.Count - 1 ? token : tree.Retain(path[i], r));
            string receiver = focusedIdentity == scopeIdentity ? token : tree.Retain(focused, r);
            // UIA trees can change between calls. A different focus invalidates
            // this observation; it does not cause a second discovery attempt.
            var latest = tree.Focused(r);
            r.Check();
            if (latest == null || tree.Identity(latest, r) != focusedIdentity)
                return new NativeCurrentReceiverEvidence("conflict", detail: "The current keyboard receiver changed during discovery.");
            return new NativeCurrentReceiverEvidence("confirmed", receiver, ancestors.ToArray());
        }
    }
}
