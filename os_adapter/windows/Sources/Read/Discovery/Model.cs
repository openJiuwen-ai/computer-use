// Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

using System;
using System.Collections.Generic;
using System.Linq;

namespace ComputerUse.Windows
{
    internal enum NativeReadDiscoveryKind
    {
        Focus,
        Point
    }

    // A single observation's OS evidence, never an input binding or a cached
    // focus/pointer owner. Only the native adapter interprets the stamp.
    internal sealed class NativeReadDiscoveryStamp
    {
        internal IntPtr Foreground, Hit;
        internal uint HitPid;
        internal long HitGeneration;
        internal int X, Y;
    }
    internal sealed class NativeReadDiscoveryGate
    {
        internal readonly NativeReadDiscoveryStamp Stamp;
        internal readonly string State, Detail;
        internal bool Eligible
        {
            get {
                return Stamp != null;
            }
        }
        internal NativeReadDiscoveryGate(NativeReadDiscoveryStamp stamp, string state = null, string detail = null)
        {
            Stamp = stamp;
            State = state;
            Detail = detail;
        }
    }
    internal sealed class NativeReadDiscoveryRetention
    {
        internal readonly string Node, State, Detail;
        internal NativeReadDiscoveryRetention(string node, string state = "complete", string detail = null)
        {
            Node = node;
            State = state;
            Detail = detail;
        }
    }
    internal interface INativeReadDiscoveryProvider
    {
        NativeReadDiscoveryGate Capture(NativeReadDiscoveryKind kind, Request request);
        object Query(NativeReadDiscoveryKind kind, NativeReadDiscoveryStamp stamp, Request request);
        bool Stable(NativeReadDiscoveryKind kind, NativeReadDiscoveryStamp stamp, Request request);
        NativeReadDiscoveryRetention Retain(NativeReadDiscoveryKind kind, NativeReadDiscoveryStamp stamp,
                                            object element, Request request);
    }
    internal interface INativeReadDiscoveryHost
    {
        IntPtr SelectedWindow { get; }
        bool WindowRoot { get; }
        bool Outside { get; }
        void Validate(Request request);
        IntPtr Foreground(Request request);
        NativeReadDiscoveryStamp Pointer(Request request);
        object Query(NativeReadDiscoveryKind kind, NativeReadDiscoveryStamp stamp, int milliseconds, Request request);
        NativeReadDiscoveryRetention Retain(object element, Action validateStable, Request request);
    }
    internal sealed class NativeReadDiscoveryResult
    {
        internal readonly Dictionary<string, object>[] Relations;
        internal readonly string[] Targets;
        internal NativeReadDiscoveryResult(IEnumerable<Dictionary<string, object>> relations)
        {
            Relations = relations.ToArray();
            Targets = Relations.SelectMany(edge => (string[])edge["targets"]).Distinct().ToArray();
        }
    }
}
