// Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

using System;
using System.Collections.Generic;
using System.Linq;

namespace ComputerUse.Windows
{
    internal interface INativeObjectProvider
    {
        IDisposable Acquire(Request request);
        NativeObjectState Read(Dictionary<string, object> target, string[] within, Request request);
        Action Prepare(NativeObjectState state, string unit, string action, string text, Request request);
    }
    // QUERY-only exact-pattern evidence for an explicitly requested action.
    // Implementations validate the retained target/scope/modal boundary after
    // provider callbacks. Semantic click/menu selection still uses full Read.
    internal interface INativeExactActionObjectProvider
    {
        NativeObjectState ReadAction(Dictionary<string, object> target, string[] within, string action,
                                     Request request);
    }
    // QUERY-only evidence for the fixed whole-control ValuePattern intent.
    // Semantic role/action catalogues are not authority for this setter.
    // Implementations validate the original boundary after provider callbacks.
    internal interface INativeValueReplacementObjectProvider
    {
        NativeObjectState ReadValueReplacement(Dictionary<string, object> target, string[] within, Request request);
    }
    // Optional condition-aware query for the selected collapsed Expand route.
    // Ordinary Read/ReadClick keep their existing provider-query sequence.
    internal interface INativeExpansionObjectProvider
    {
        NativeObjectState ReadExpansion(Dictionary<string, object> target, string[] within, Request request);
    }
    // Optional v1 observation port. Semantic metadata may be unavailable while
    // the retained target still has an independently validated pointer route.
    // Implementations must revalidate identity/scope after a metadata failure.
    internal interface INativeClickObjectProvider
    {
        NativeObjectState ReadClick(Dictionary<string, object> target, string[] within, Request request);
    }
}
