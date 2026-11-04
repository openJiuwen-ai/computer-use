// Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

using System;
using System.Collections.Generic;
using System.Windows.Automation;

namespace ComputerUse.Windows
{
    // QUERY evidence, never an input permit. Missing provider facts must not
    // turn an observed relation into either a confirmed owner or a lost node.
    internal enum NativeOwnershipState
    {
        Confirmed,
        Unknown,
        Outside
    }

    internal sealed class NativeObservationScope
    {
        internal readonly NativeOwnershipState State;
        internal readonly string Detail;
        internal readonly bool RestrictInput;
        internal NativeObservationScope(NativeOwnershipState state, string detail, bool restrictInput = false)
        {
            State = state;
            Detail = detail ?? "";
            RestrictInput = restrictInput || state == NativeOwnershipState.Outside;
        }
        internal string Name
        {
            get {
                return State.ToString().ToLowerInvariant();
            }
        }
        internal bool Confirmed
        {
            get {
                return State == NativeOwnershipState.Confirmed;
            }
        }
        internal static NativeObservationScope Unknown(string detail)
        {
            return new NativeObservationScope(NativeOwnershipState.Unknown, detail, true);
        }
        internal static NativeObservationScope DeferredRelation
        {
            get {
                return new NativeObservationScope(
                    NativeOwnershipState.Unknown,
                    "Observed native relation without a native HWND; input requires independent live identity and window validation.");
            }
        }
    }

    internal interface INativeObservationAncestry
    {
        string Identity(object element);
        IntPtr Handle(object element);
        bool Inside(IntPtr handle);
        object Parent(object element);
        bool Desktop(object element);
    }

    internal static class NativeObservationOwnership
    {
        internal static void PropagateBoundary(Exception error, Request request)
        {
            request.Check();
            var failure = error as Failure;
            if (failure != null && (failure.Code == "cancelled" || failure.Code == "deadline_exceeded"))
                throw failure;
        }
        // A foreign HWND alone is not outside: hosted fragments can cross
        // HWND/process boundaries. Only a completed path to the UIA desktop
        // proves outside; an incomplete/broken provider path remains unknown.
        internal static NativeObservationScope Read(object element, INativeObservationAncestry tree, Request request)
        {
            var seen = new HashSet<string>();
            string incomplete = "";
            bool foreignAncestor = false;
            for (int depth = 0; depth < 128 && element != null; depth++)
            {
                request.Check();
                IntPtr handle = IntPtr.Zero;
                try
                {
                    handle = tree.Handle(element);
                    if (tree.Inside(handle))
                        return new NativeObservationScope(NativeOwnershipState.Confirmed,
                                                          "Native HWND belongs to the selected window." + incomplete,
                                                          incomplete.Length != 0);
                }
                catch (Exception error)
                {
                    PropagateBoundary(error, request);
                    incomplete = " Native HWND query unavailable: " + error.Message;
                }
                try
                {
                    string identity = tree.Identity(element);
                    if (!String.IsNullOrEmpty(identity) && !seen.Add(identity))
                        return NativeObservationScope.Unknown(
                            "Raw View ancestry repeated a runtime identity before ownership could be confirmed.");
                }
                catch (Exception error)
                {
                    PropagateBoundary(error, request);
                    incomplete = " Runtime identity query unavailable: " + error.Message;
                }
                object parent;
                try
                {
                    parent = tree.Parent(element);
                }
                catch (Exception error)
                {
                    PropagateBoundary(error, request);
                    return NativeObservationScope.Unknown("Raw View parent unavailable: " + error.Message);
                }
                if (parent == null)
                {
                    try
                    {
                        if (tree.Desktop(element))
                        {
                            if (incomplete.Length != 0 || (!foreignAncestor && depth != 0))
                                return NativeObservationScope.Unknown(
                                    "Raw View reached desktop but missing native identity/ownership facts cannot exclude the selected window." +
                                    incomplete);
                            return new NativeObservationScope(
                                NativeOwnershipState.Outside,
                                "Raw View ancestry reached the UIA desktop without the selected window; select the independent window before input or further traversal.");
                        }
                    }
                    catch (Exception error)
                    {
                        PropagateBoundary(error, request);
                    }
                    return NativeObservationScope.Unknown(
                        "Raw View ancestry ended before the selected window or UIA desktop could be confirmed.");
                }
                if (handle != IntPtr.Zero)
                    foreignAncestor = true;
                element = parent;
            }
            request.Check();
            return NativeObservationScope.Unknown(
                "Raw View ancestry could not establish ownership within its bounded parent path.");
        }
    }

    internal enum NativeObservationIdentityState
    {
        Same,
        Changed,
        Ambiguous
    }
    internal static class NativeObservationIdentity
    {
        // RuntimeId is opaque and reusable. Refreshing a retained input
        // reference requires the native comparison, not a new COM pointer or
        // a same-name/runtime guess. Inconclusive queries retain the old ref.
        internal static NativeObservationIdentityState Verify(string runtime, Func<string> previousRuntime,
                                                              Func<bool> compare, Request request)
        {
            request.Check();
            try
            {
                if (previousRuntime() != runtime)
                    return NativeObservationIdentityState.Changed;
                bool same = compare();
                request.Check();
                return same ? NativeObservationIdentityState.Same : NativeObservationIdentityState.Ambiguous;
            }
            catch (Exception error)
            {
                NativeObservationOwnership.PropagateBoundary(error, request);
                return NativeObservationIdentityState.Ambiguous;
            }
        }
    }

    internal static class NativeCapturedNodeValidation
    {
        internal static void Verify(string expectedRuntime, Func<string> identity, Action liveness, Request request)
        {
            request.Check();
            try
            {
                string current = identity();
                request.Check();
                if (expectedRuntime != null && current != expectedRuntime)
                    throw new Failure(
                        "snapshot_expired",
                        "captured native observation identity changed; the old reference was not rebound");
            }
            catch (ElementNotAvailableException)
            {
                throw new Failure("snapshot_expired", "captured native observation object no longer exists");
            }
            catch (Failure)
            {
                throw;
            }
            catch (Exception error)
            {
                NativeObservationOwnership.PropagateBoundary(error, request);
            }
            // A generic RuntimeId provider error cannot prove destruction.
            // Query the retained object itself; only UIA's explicit destroyed
            // object signal expires the page. Neither query renews authority.
            try
            {
                liveness();
                request.Check();
            }
            catch (ElementNotAvailableException)
            {
                throw new Failure("snapshot_expired", "captured native observation object was destroyed");
            }
            catch (Failure)
            {
                throw;
            }
            catch (Exception error)
            {
                NativeObservationOwnership.PropagateBoundary(error, request);
            }
        }
    }

    // The restriction is fixed when the reference is created. A read refresh
    // cannot upgrade it; a separately confirmed reference uses another key.
    internal sealed class NativeObservationAuthority
    {
        internal readonly bool ObservationOnly;
        internal NativeObservationAuthority(NativeObservationScope ownership, string runtime, bool inherited)
        {
            ObservationOnly = inherited || ownership.RestrictInput || String.IsNullOrEmpty(runtime);
        }
        internal string IdentityKey(string surface, string runtime)
        {
            return surface + ":" + (ObservationOnly ? "observation:" : "input:") + runtime;
        }
        internal void RequireInput()
        {
            if (ObservationOnly)
                throw new Failure(
                    "stale_target",
                    "this UIA reference was retained for observation only; reading it cannot grant input authority",
                    "select a separately confirmed element or independent window; use a current screenshot for coordinate input when UIA ownership or identity remains unknown");
        }
    }
}
